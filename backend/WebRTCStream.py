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


class _AiortcStream:
    """Create and clean up one RTSP-backed WebRTC peer per viewer."""

    def __init__(self, camera_url: str, logger: logging.Logger) -> None:
        self._camera_url = camera_url
        self._logger = logger
        self._sessions: dict[RTCPeerConnection, MediaPlayer] = {}
        self._lock = asyncio.Lock()

    async def update_url(self, camera_url: str) -> None:
        async with self._lock:
            self._camera_url = camera_url
            sessions = tuple(self._sessions)

        await asyncio.gather(*(self.close_peer(peer) for peer in sessions))

    async def create_answer(
        self, offer: RTCSessionDescription
    ) -> RTCSessionDescription:
        async with self._lock:
            camera_url = self._camera_url

        if not camera_url:
            raise ValueError("camera_url is not configured")

        peer = RTCPeerConnection()
        player: MediaPlayer | None = None
        try:
            player = MediaPlayer(
                camera_url,
                format="rtsp",
                options={
                    "rtsp_transport": "udp",
                    "fflags": "nobuffer",
                    "flags": "low_delay",
                },
            )
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
                self._sessions[peer] = player
            return peer.localDescription
        except Exception:
            if player is not None:
                player.video and player.video.stop()
                player.audio and player.audio.stop()
            await peer.close()
            raise

    async def close_peer(self, peer: RTCPeerConnection) -> None:
        async with self._lock:
            player = self._sessions.pop(peer, None)

        if player is not None:
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
    """Proxy RTSP/WebRTC signaling through one local go2rtc process."""

    STREAM_NAME = "robot-camera"
    API_URL = "http://127.0.0.1:1984"
    API_TIMEOUT_SECONDS = 15

    def __init__(self, camera_url: str, logger: logging.Logger) -> None:
        self._logger = logger
        self._camera_url = camera_url
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
        config = """api:\n  listen: 127.0.0.1:1984\nwebrtc:\n  listen: :8555\nstreams:\n  robot-camera:\n"""
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

    async def _ensure_stream(self, camera_url: str) -> None:
        if self._process is None or self._process.poll() is not None:
            await self._start()
        query = urlencode({"name": self.STREAM_NAME, "src": camera_url})
        await self._api_request(
            "PUT",
            f"/api/streams?{query}",
            phase="RTSP stream registration",
        )
        self._camera_url = camera_url

    async def update_url(self, camera_url: str) -> None:
        async with self._lock:
            self._camera_url = camera_url
            if not camera_url:
                return
            await self._ensure_stream(camera_url)

    async def create_answer(
        self, offer: RTCSessionDescription
    ) -> RTCSessionDescription:
        async with self._lock:
            if not self._camera_url:
                raise ValueError("camera_url is not configured")
            await self._ensure_stream(self._camera_url)
            body = offer.sdp.encode()
            answer_sdp = await self._api_request(
                "POST",
                f"/api/webrtc?src={self.STREAM_NAME}",
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
        self, camera_url: str, logger: logging.Logger, backend: str = "go2rtc"
    ) -> None:
        self._logger = logger
        self._backend = backend
        self._camera_url = camera_url
        self._stream: _AiortcStream | _Go2RtcStream
        self._set_backend(backend)

    def _set_backend(self, backend: str) -> None:
        if backend == "aiortc":
            self._stream = _AiortcStream(self._camera_url, self._logger)
        elif backend == "go2rtc":
            self._stream = _Go2RtcStream(self._camera_url, self._logger)
        else:
            raise ValueError("camera_backend must be 'go2rtc' or 'aiortc'")

    async def update_config(self, camera_url: str, backend: str) -> None:
        if backend != self._backend:
            await self.close()
            self._backend = backend
            self._camera_url = camera_url
            self._set_backend(backend)
            return
        self._camera_url = camera_url
        await self._stream.update_url(camera_url)

    async def create_answer(
        self, offer: RTCSessionDescription
    ) -> RTCSessionDescription:
        return await self._stream.create_answer(offer)

    async def close(self) -> None:
        await self._stream.close()
