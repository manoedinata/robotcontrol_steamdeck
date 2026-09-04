import asyncio
import logging
import os
import signal
import subprocess
import tempfile
from contextlib import suppress
from socket import timeout as SocketTimeout
from urllib.parse import urlencode
from urllib.error import URLError
from urllib.request import Request, urlopen

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer

# A config carries every RTSP source that should stay warm as (id, url) pairs.
CameraStreams = tuple[tuple[str, str], ...]


def _resolve_stream_id(streams: dict[str, str], requested: str | None) -> str:
    """Pick the stream a WebRTC offer targets.

    The renderer names the stream through ``?src=<id>``; when it is omitted
    (or empty) and exactly one stream is configured, fall back to that one so
    single-camera setups keep working without the query parameter.
    """
    if requested:
        if requested not in streams:
            raise ValueError(f"unknown camera stream: {requested!r}")
        return requested
    if len(streams) == 1:
        return next(iter(streams))
    if not streams:
        raise ValueError("no camera stream is configured")
    raise ValueError("camera stream id (?src=) is required with multiple streams")


class _AiortcStream:
    """Create and clean up one RTSP-backed WebRTC peer per viewer."""

    # Microseconds. ffmpeg waits ~7s (or forever, on a camera that accepts the
    # connection then goes quiet) before giving up on an RTSP dial, so bound it.
    RTSP_OPEN_TIMEOUT_US = "5000000"

    def __init__(self, streams: CameraStreams, logger: logging.Logger) -> None:
        self._streams: dict[str, str] = dict(streams)
        self._logger = logger
        # Each peer remembers the stream id and URL it was opened with so a
        # config change can close only the peers whose source actually changed.
        self._sessions: dict[
            RTCPeerConnection, tuple[str, str, MediaPlayer]
        ] = {}
        # Stream ids with an RTSP dial in flight. The renderer retries a failed
        # camera every couple of seconds, which is faster than a dead camera
        # fails, so without this the retries would stack worker threads.
        self._dialing: set[str] = set()
        self._lock = asyncio.Lock()

    def _open_player(self, camera_url: str) -> MediaPlayer:
        """Dial the RTSP source. Blocking: only call this in a worker thread."""
        return MediaPlayer(
            camera_url,
            format="rtsp",
            options={
                "rtsp_transport": "udp",
                "fflags": "nobuffer",
                "flags": "low_delay",
                "timeout": self.RTSP_OPEN_TIMEOUT_US,
            },
        )

    async def update_streams(self, streams: CameraStreams) -> None:
        async with self._lock:
            self._streams = dict(streams)
            stale = [
                peer
                for peer, (stream_id, url, _) in self._sessions.items()
                if self._streams.get(stream_id) != url
            ]

        await asyncio.gather(*(self.close_peer(peer) for peer in stale))

    async def create_answer(
        self, offer: RTCSessionDescription, stream_id: str | None = None
    ) -> RTCSessionDescription:
        async with self._lock:
            resolved_id = _resolve_stream_id(self._streams, stream_id)
            camera_url = self._streams[resolved_id]
            if resolved_id in self._dialing:
                raise RuntimeError(
                    f"camera stream {resolved_id!r} is already being opened"
                )
            self._dialing.add(resolved_id)

        peer = RTCPeerConnection()
        player: MediaPlayer | None = None
        try:
            # MediaPlayer() opens the RTSP container synchronously and does not
            # return until the camera answers or ffmpeg times out. Run it in a
            # worker thread so an unreachable camera cannot stall the event
            # loop, and with it the UDP send loop, telemetry and PTZ.
            player = await asyncio.to_thread(self._open_player, camera_url)
            if player.video is None:
                raise RuntimeError("RTSP source does not provide a video track")

            peer.addTrack(player.video)

            @peer.on("connectionstatechange")
            async def on_connectionstatechange() -> None:
                if peer.connectionState in {"failed", "closed", "disconnected"}:
                    await self.close_peer(peer)

            await peer.setRemoteDescription(offer)
            answer = await peer.createAnswer()
            await peer.setLocalDescription(answer)

            async with self._lock:
                self._sessions[peer] = (resolved_id, camera_url, player)
            return peer.localDescription
        except Exception:
            if player is not None:
                player.video and player.video.stop()
                player.audio and player.audio.stop()
            await peer.close()
            raise
        finally:
            async with self._lock:
                self._dialing.discard(resolved_id)

    async def close_peer(self, peer: RTCPeerConnection) -> None:
        async with self._lock:
            session = self._sessions.pop(peer, None)

        if session is not None:
            _, _, player = session
            if player.video is not None:
                player.video.stop()
            if player.audio is not None:
                player.audio.stop()
        if peer.connectionState != "closed":
            await peer.close()

    async def close(self) -> None:
        async with self._lock:
            peers = tuple(self._sessions)
        await asyncio.gather(*(self.close_peer(peer) for peer in peers))


class _Go2RtcStream:
    """Proxy RTSP/WebRTC signaling through one local go2rtc process.

    go2rtc keeps a source connected while at least one consumer is attached,
    so holding a WebRTC peer open per stream is what keeps every configured
    camera warm for an instant switch.
    """

    API_URL = "http://127.0.0.1:1984"
    API_TIMEOUT_SECONDS = 15

    def __init__(self, streams: CameraStreams, logger: logging.Logger) -> None:
        self._logger = logger
        self._streams: dict[str, str] = dict(streams)
        self._registered: dict[str, str] = {}
        self._process: subprocess.Popen[bytes] | None = None
        self._config_path: str | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _request(
        method: str,
        url: str,
        body: bytes | None = None,
        content_type: str = "application/json",
    ) -> bytes:
        request = Request(
            url,
            data=body,
            method=method,
            headers={"Content-Type": content_type} if body is not None else {},
        )
        with urlopen(request, timeout=_Go2RtcStream.API_TIMEOUT_SECONDS) as response:
            return response.read()

    async def _api_request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        content_type: str = "application/json",
        phase: str = "go2rtc API request",
    ) -> bytes:
        try:
            return await asyncio.to_thread(
                self._request, method, f"{self.API_URL}{path}", body, content_type
            )
        except (OSError, SocketTimeout) as error:
            self._logger.warning(
                "go2rtc %s failed for %s %s: %s",
                phase,
                method,
                path.split("?", 1)[0],
                error,
            )
            raise

    async def _start(self) -> None:
        binary = os.environ.get("GO2RTC_BINARY", "go2rtc")
        try:
            process = subprocess.Popen(
                [binary, "-config", self._create_config_file()],
                stdin=subprocess.DEVNULL,
                stdout=None,
                stderr=None,
                start_new_session=True,
            )
        except OSError as error:
            self._logger.error("Failed to start go2rtc backend: %s", error)
            raise RuntimeError(
                f"go2rtc backend is unavailable: could not start {binary!r}"
            ) from error

        self._process = process
        self._registered = {}
        for _ in range(20):
            if process.poll() is not None:
                await self._stop_process()
                raise RuntimeError("go2rtc backend exited during startup")
            try:
                await self._api_request("GET", "/api", phase="startup health check")
                return
            except (OSError, URLError):
                await asyncio.sleep(0.1)

        await self._stop_process()
        raise RuntimeError("go2rtc backend did not become ready")

    def _create_config_file(self) -> str:
        config = "api:\n  listen: 127.0.0.1:1984\nwebrtc:\n  listen: :8555\n"
        handle = tempfile.NamedTemporaryFile(
            mode="w", prefix="robot-monitor-go2rtc-", suffix=".yaml", delete=False
        )
        with handle:
            handle.write(config)
        self._config_path = handle.name
        return handle.name

    async def _stop_process(self) -> None:
        process = self._process
        self._process = None
        self._registered = {}
        if process is None:
            return
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                await asyncio.to_thread(process.wait, 3)
            except (OSError, subprocess.TimeoutExpired):
                with suppress(Exception):
                    os.killpg(process.pid, signal.SIGKILL)
                process.kill()
                await asyncio.to_thread(process.wait)
        if self._config_path:
            try:
                os.unlink(self._config_path)
            except OSError:
                pass
            self._config_path = None

    async def _sync_streams(self) -> None:
        """Register/refresh/drop go2rtc streams to match the config."""
        if self._process is None or self._process.poll() is not None:
            await self._start()

        for stream_id, url in self._streams.items():
            if self._registered.get(stream_id) == url:
                continue
            if stream_id in self._registered:
                # The URL changed; drop the stale source before re-adding so
                # go2rtc does not keep both.
                with suppress(OSError, SocketTimeout):
                    await self._api_request(
                        "DELETE",
                        f"/api/streams?src={stream_id}",
                        phase="RTSP stream refresh",
                    )
            query = urlencode({"name": stream_id, "src": url})
            await self._api_request(
                "PUT",
                f"/api/streams?{query}",
                phase="RTSP stream registration",
            )
            self._registered[stream_id] = url

        for stream_id in tuple(self._registered):
            if stream_id in self._streams:
                continue
            with suppress(OSError, SocketTimeout):
                await self._api_request(
                    "DELETE",
                    f"/api/streams?src={stream_id}",
                    phase="RTSP stream removal",
                )
            self._registered.pop(stream_id, None)

    async def update_streams(self, streams: CameraStreams) -> None:
        async with self._lock:
            self._streams = dict(streams)
            if not self._streams:
                await self._stop_process()
                return
            await self._sync_streams()

    async def create_answer(
        self, offer: RTCSessionDescription, stream_id: str | None = None
    ) -> RTCSessionDescription:
        async with self._lock:
            resolved_id = _resolve_stream_id(self._streams, stream_id)
            await self._sync_streams()
            body = offer.sdp.encode()
            answer_sdp = await self._api_request(
                "POST",
                f"/api/webrtc?src={resolved_id}",
                body,
                "application/sdp",
                "WebRTC SDP exchange",
            )
            return RTCSessionDescription(
                sdp=answer_sdp.decode(),
                type="answer",
            )

    async def close(self) -> None:
        async with self._lock:
            await self._stop_process()


class WebRTCStream:
    """Select and manage the configured RTSP-to-WebRTC backend."""

    def __init__(
        self,
        streams: CameraStreams,
        logger: logging.Logger,
        backend: str = "go2rtc",
    ) -> None:
        self._logger = logger
        self._backend = backend
        self._streams: CameraStreams = tuple(streams)
        self._stream: _AiortcStream | _Go2RtcStream
        self._set_backend(backend)

    def _set_backend(self, backend: str) -> None:
        if backend == "aiortc":
            self._stream = _AiortcStream(self._streams, self._logger)
        elif backend == "go2rtc":
            self._stream = _Go2RtcStream(self._streams, self._logger)
        else:
            raise ValueError("camera_backend must be 'go2rtc' or 'aiortc'")

    async def update_config(self, streams: CameraStreams, backend: str) -> None:
        streams = tuple(streams)
        if backend != self._backend:
            await self.close()
            self._backend = backend
            self._streams = streams
            self._set_backend(backend)
            if streams:
                await self._stream.update_streams(streams)
            return
        self._streams = streams
        await self._stream.update_streams(streams)

    async def create_answer(
        self, offer: RTCSessionDescription, stream_id: str | None = None
    ) -> RTCSessionDescription:
        return await self._stream.create_answer(offer, stream_id)

    async def close(self) -> None:
        await self._stream.close()
