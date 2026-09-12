import asyncio
import logging
import os
import signal
import subprocess
import tempfile
from contextlib import suppress
from socket import timeout as SocketTimeout
from typing import Any
from urllib.parse import urlencode
from urllib.error import URLError
from urllib.request import Request, urlopen

import av
from aiortc import (
    MediaStreamTrack,
    RTCPeerConnection,
    RTCRtpSender,
    RTCSessionDescription,
)
from aiortc.contrib.media import MediaPlayer, MediaRelay

import utils
from CameraWebSocketSource import (
    CameraWebSocketHub,
    SourceReader,
    annexb_nal_types,
    is_websocket_url,
    relay_url,
)

# A config carries every camera source that should stay warm as (id, url)
# pairs. A url is either an RTSP source dialed directly, or a WebSocket source
# the backend re-serves over HTTP (see CameraWebSocketSource).
CameraStreams = tuple[tuple[str, str], ...]


def ingest_url(stream_id: str, url: str) -> str:
    """The address the camera backend should actually open for one source.

    RTSP passes straight through. A WebSocket source is not something go2rtc or
    ffmpeg can dial, so it is read from the local relay that re-serves it.
    """
    return relay_url(stream_id) if is_websocket_url(url) else url


def go2rtc_source(stream_id: str, url: str, input_format: str) -> str:
    """The go2rtc source string for one camera.

    A relayed source is registered as `exec:` rather than as a plain URL: the
    relay serves a bare bytestream with no container to identify it, so ffmpeg
    has to be told the demuxer, and only the exec form lets us pass it.
    """
    if not is_websocket_url(url):
        return url
    return (
        "exec:ffmpeg -hide_banner -loglevel error"
        " -fflags nobuffer -flags low_delay"
        f" -use_wallclock_as_timestamps 1 -f {input_format}"
        f" -i {ingest_url(stream_id, url)}"
        " -c:v copy -f rtsp {output}"
    )


# NAL unit type 7, a sequence parameter set. A packet that already opens with
# one needs nothing prepended.
_H264_SPS = 7


def missing_parameter_sets(packet: Any) -> bytes:
    """The parameter sets a packet needs prepended, or nothing.

    RTSP carries them in the SDP rather than in the stream, so a packet taken
    straight off the wire has none and anything reading the result as a bare
    bytestream -- a browser's decoder, or the recorder's demuxer -- has nothing
    to configure itself with. They go in front of each keyframe instead, which
    is what ffmpeg's ``dump_extra`` bitstream filter does and is legal to
    repeat.
    """
    if not packet.is_keyframe:
        return b""
    if annexb_nal_types(bytes(packet))[:1] == (_H264_SPS,):
        return b""
    extradata = packet.stream.codec_context.extradata
    return bytes(extradata) if extradata else b""


class _AnnexBTrack(MediaStreamTrack):
    """One camera's packets, carrying the parameter sets a decoder needs.

    Wrapping the player rather than each consumer means the peers and the
    recording see the same bytes, and the camera is still read exactly once.
    """

    kind = "video"

    def __init__(self, source: MediaStreamTrack) -> None:
        super().__init__()
        self._source = source

    async def recv(self) -> Any:
        packet = await self._source.recv()
        prefix = missing_parameter_sets(packet)
        if not prefix:
            return packet
        # av.Packet is not mutable in place, and the sender needs only the
        # bytes and the timestamp.
        stamped = av.Packet(prefix + bytes(packet))
        stamped.pts = packet.pts
        stamped.dts = packet.dts
        stamped.time_base = packet.time_base
        return stamped


class _SharedPlayer:
    """One camera connection, fanned out to every consumer of one source.

    The aiortc backend used to open a player per WebRTC peer. Sharing one means
    a camera sees a single session however many peers are attached -- and, more
    to the point, that recording can read the connection the live view already
    holds instead of dialing the camera a second time.
    """

    def __init__(
        self, stream_id: str, url: str, player: MediaPlayer, decoded: bool
    ) -> None:
        self.stream_id = stream_id
        self.url = url
        self.player = player
        # Whether the player hands out decoded frames. MJPEG has to be decoded
        # and re-encoded because no browser accepts it over WebRTC; H.264 is
        # forwarded exactly as the camera sent it, which is both cheaper and
        # the only form a recording can stream-copy.
        self.decoded = decoded
        # MJPEG is decoded, so there is nothing to repair; H.264 packets get
        # their parameter sets put back once, here, for every consumer.
        self.source = player.video if decoded else _AnnexBTrack(player.video)
        self.relay = MediaRelay()
        self.holders = 0
        # Feeds the relay endpoint's buffer for as long as this connection
        # lives, so a recording started later begins at a keyframe.
        self.tap: asyncio.Task | None = None
        # Set when the camera stops being readable, so the hub can retry.
        self.ended = asyncio.Event()

    def track(self) -> Any:
        """A proxy track for one more consumer of this connection."""
        return self.relay.subscribe(self.source)


def _prefer_h264(peer: RTCPeerConnection) -> None:
    """Pin the video transceiver to H.264.

    Forwarding the camera's own packets only works if the peer negotiated the
    codec they are already in. Without this aiortc may answer with its default
    preference order and the packets would be packed as something they are not.
    """
    codecs = [
        codec
        for codec in RTCRtpSender.getCapabilities("video").codecs
        if codec.mimeType in {"video/H264", "video/rtx"}
    ]
    if not codecs:
        return
    for transceiver in peer.getTransceivers():
        if transceiver.kind == "video":
            transceiver.setCodecPreferences(codecs)


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


def _discard_dialed_player(dial: asyncio.Future) -> None:
    """Tear down a player that finished opening after we stopped waiting."""
    if dial.cancelled() or dial.exception() is not None:
        return
    with suppress(Exception):
        _stop_player(dial.result())


def _stop_player(player: MediaPlayer | None) -> None:
    """Release the RTSP connection behind a player."""
    if player is None:
        return
    if player.video is not None:
        player.video.stop()
    if player.audio is not None:
        player.audio.stop()


class _AiortcStream:
    """Serve WebRTC peers from one shared connection per camera.

    One connection, not one per viewer: peers take a proxy of it, and the relay
    takes another, so a recording reads the session the live view already holds
    rather than asking the camera for a second one. H.264 is forwarded exactly
    as the camera sent it -- no decode, no encode, and in a form a recording can
    stream-copy. MJPEG still has to be transcoded, because no browser will take
    it over WebRTC.
    """

    # Microseconds. ffmpeg waits ~7s (or forever, on a camera that accepts the
    # connection then goes quiet) before giving up on an RTSP dial, so bound it.
    RTSP_OPEN_TIMEOUT_US = "5000000"

    def __init__(
        self,
        streams: CameraStreams,
        logger: logging.Logger,
        ws_hub: CameraWebSocketHub | None = None,
    ) -> None:
        self._streams: dict[str, str] = dict(streams)
        self._logger = logger
        self._ws_hub = ws_hub
        # Each peer remembers the stream id and URL it was opened with so a
        # config change can close only the peers whose source actually changed.
        self._sessions: dict[RTCPeerConnection, tuple[str, str, _SharedPlayer]] = {}
        # One connection per source, however many peers and recorders read it.
        self._shared: dict[str, _SharedPlayer] = {}
        # Serializes opening one source. Not self._lock: dialing a camera takes
        # seconds, and that lock must stay free for config updates.
        self._acquiring: dict[str, asyncio.Lock] = {}
        # Stream ids with an RTSP dial in flight. The renderer retries a failed
        # camera every couple of seconds, which is faster than a dead camera
        # fails, so without this the retries would stack worker threads.
        self._dialing: set[str] = set()
        # Set by close(). A dial in flight stops being waited on, so quitting
        # mid-reconnect does not wait out the camera's timeout, and whatever
        # the dial eventually opens is torn down instead of left running.
        self._closing = asyncio.Event()
        self._lock = asyncio.Lock()

    def _open_player(self, camera_url: str, input_format: str | None) -> MediaPlayer:
        """Dial the camera source. Blocking: only call this in a worker thread."""
        if input_format is not None:
            # A relayed WebSocket source: a bare bytestream over HTTP, so the
            # demuxer has to be named and the packets need wallclock stamps
            # because the stream carries no timing of its own.
            return MediaPlayer(
                camera_url,
                format=input_format,
                options={
                    "fflags": "nobuffer",
                    "flags": "low_delay",
                    "use_wallclock_as_timestamps": "1",
                },
            )
        return MediaPlayer(
            camera_url,
            format="rtsp",
            options={
                "rtsp_transport": "udp",
                "fflags": "nobuffer",
                "flags": "low_delay",
                "timeout": self.RTSP_OPEN_TIMEOUT_US,
            },
            # Hand out the camera's own packets instead of decoding them. The
            # Deck stops paying for a decode and an encode per peer, and the
            # packets stay in a form a recording can stream-copy -- which is
            # what lets one connection serve both.
            decode=False,
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
        shared: _SharedPlayer | None = None
        try:
            input_format = await self._input_format(resolved_id, camera_url)
            shared = await self._acquire(resolved_id, camera_url, input_format)

            peer.addTrack(shared.track())
            if not shared.decoded:
                _prefer_h264(peer)

            @peer.on("connectionstatechange")
            async def on_connectionstatechange() -> None:
                if peer.connectionState in {"failed", "closed", "disconnected"}:
                    await self.close_peer(peer)

            await peer.setRemoteDescription(offer)
            answer = await peer.createAnswer()
            await peer.setLocalDescription(answer)

            async with self._lock:
                self._sessions[peer] = (resolved_id, camera_url, shared)
            return peer.localDescription
        except Exception:
            if shared is not None:
                await self._release(shared)
            await peer.close()
            raise
        finally:
            async with self._lock:
                self._dialing.discard(resolved_id)

    async def _input_format(self, stream_id: str, camera_url: str) -> str | None:
        """The demuxer for a relayed source, or None when the source is RTSP."""
        if not is_websocket_url(camera_url) or self._ws_hub is None:
            return None
        return await self._ws_hub.detect_format(stream_id, camera_url)

    async def _dial(
        self, stream_id: str, camera_url: str, input_format: str | None
    ) -> MediaPlayer:
        """Open the camera source, giving up the wait as soon as we are closing."""
        dial = utils.run_detached(
            self._open_player,
            ingest_url(stream_id, camera_url),
            input_format,
            thread_name=f"camera-dial-{stream_id}",
        )
        closing = asyncio.ensure_future(self._closing.wait())
        try:
            await asyncio.wait({dial, closing}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            closing.cancel()

        if not dial.done():
            # Shutting down. Stop waiting on the camera and hand the player,
            # whenever it lands, straight to teardown.
            dial.add_done_callback(_discard_dialed_player)
            raise RuntimeError("camera backend is shutting down")
        return dial.result()

    async def _acquire(
        self, stream_id: str, camera_url: str, input_format: str | None
    ) -> _SharedPlayer:
        """Take a hold on this source's connection, opening it if nobody has."""
        lock = self._acquiring.setdefault(stream_id, asyncio.Lock())
        async with lock:
            shared = self._shared.get(stream_id)
            if shared is not None and shared.url == camera_url:
                shared.holders += 1
                return shared

            # MediaPlayer() opens the container synchronously and does not
            # return until the camera answers or ffmpeg times out. Run it off
            # the event loop so an unreachable camera cannot stall the UDP send
            # loop, telemetry and PTZ along with it.
            player = await self._dial(stream_id, camera_url, input_format)
            if player.video is None:
                _stop_player(player)
                raise RuntimeError("camera source does not provide a video track")

            shared = _SharedPlayer(
                stream_id, camera_url, player, decoded=input_format is not None
            )
            shared.holders = 1
            if not shared.decoded and self._ws_hub is not None:
                if self._ws_hub.has_stream(stream_id):
                    shared.tap = asyncio.create_task(
                        self._tap(shared), name=f"camera-tap-{stream_id}"
                    )
            previous = self._shared.get(stream_id)
            self._shared[stream_id] = shared
            if previous is not None:
                # The url changed under us; nothing new may join the old
                # connection, and it goes when its last holder leaves.
                previous.url = ""
            return shared

    async def _release(self, shared: _SharedPlayer) -> None:
        """Give up one hold, closing the connection when the last one goes."""
        lock = self._acquiring.setdefault(shared.stream_id, asyncio.Lock())
        async with lock:
            shared.holders -= 1
            if shared.holders > 0:
                return
            if self._shared.get(shared.stream_id) is shared:
                del self._shared[shared.stream_id]
            tap = shared.tap
            shared.tap = None
            if tap is not None:
                tap.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await tap
            _stop_player(shared.player)

    def packet_source(self, stream_id: str, camera_url: str) -> SourceReader:
        """A reader the hub can use to serve this source from the relay.

        This is what makes recording cost no second camera session: the bytes
        come off the connection the live view is already holding. The hold is
        taken when a subscriber attaches and given up when it leaves, so a
        recording keeps the camera open even after every peer has gone.
        """

        async def reader() -> None:
            shared = await self._acquire(stream_id, camera_url, None)
            try:
                # The tap is what publishes; this only holds the camera open
                # for as long as the relay has someone reading it, and hands
                # the hub back its retry when the connection ends.
                await shared.ended.wait()
                raise RuntimeError(f"camera stream {stream_id!r} ended")
            finally:
                # Shielded: this runs while the hub is cancelling the reader,
                # and giving up the hold half way would leave the camera
                # session open for the life of the process.
                with suppress(asyncio.CancelledError):
                    await asyncio.shield(self._release(shared))

        return reader

    async def _tap(self, shared: _SharedPlayer) -> None:
        """Feed one camera's packets to the relay for as long as it is held."""
        assert self._ws_hub is not None
        track = shared.track()
        try:
            while True:
                packet = await track.recv()
                self._ws_hub.publish(shared.stream_id, bytes(packet))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._logger.warning(
                "Camera %s stopped being readable: %s", shared.stream_id, error
            )
        finally:
            track.stop()
            shared.ended.set()

    async def close_peer(self, peer: RTCPeerConnection) -> None:
        async with self._lock:
            session = self._sessions.pop(peer, None)

        if session is not None:
            await self._release(session[2])
        if peer.connectionState != "closed":
            await peer.close()

    async def close(self) -> None:
        self._closing.set()
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

    def __init__(
        self,
        streams: CameraStreams,
        logger: logging.Logger,
        ws_hub: CameraWebSocketHub | None = None,
    ) -> None:
        self._logger = logger
        self._streams: dict[str, str] = dict(streams)
        self._ws_hub = ws_hub
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
            return await utils.run_detached(
                self._request,
                method,
                f"{self.API_URL}{path}",
                body,
                content_type,
                thread_name="go2rtc-api",
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
                await utils.run_detached(process.wait, 3, thread_name="go2rtc-wait")
            except (OSError, subprocess.TimeoutExpired):
                with suppress(Exception):
                    os.killpg(process.pid, signal.SIGKILL)
                process.kill()
                await utils.run_detached(process.wait, thread_name="go2rtc-wait")
        if self._config_path:
            try:
                os.unlink(self._config_path)
            except OSError:
                pass
            self._config_path = None

    async def _source_string(self, stream_id: str, url: str) -> str:
        """What go2rtc should be told to open for one configured source."""
        if not is_websocket_url(url):
            return url
        input_format = "h264"
        if self._ws_hub is not None:
            input_format = await self._ws_hub.detect_format(stream_id, url)
        return go2rtc_source(stream_id, url, input_format)

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
            source = await self._source_string(stream_id, url)
            query = urlencode({"name": stream_id, "src": source})
            await self._api_request(
                "PUT",
                f"/api/streams?{query}",
                phase="camera stream registration",
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

        # The SDP exchange waits on go2rtc dialing the camera, which is bounded
        # only by API_TIMEOUT_SECONDS. Holding the lock across it would stall
        # every config update -- and with it the control WebSocket that sends
        # them -- for the whole timeout, so it runs unlocked. Registration
        # above stays serialized, which is what the lock is actually for.
        answer_sdp = await self._api_request(
            "POST",
            f"/api/webrtc?src={resolved_id}",
            offer.sdp.encode(),
            "application/sdp",
            "WebRTC SDP exchange",
        )
        return RTCSessionDescription(sdp=answer_sdp.decode(), type="answer")

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
        ws_hub: CameraWebSocketHub | None = None,
    ) -> None:
        self._logger = logger
        self._backend = backend
        self._streams: CameraStreams = tuple(streams)
        # Resolves the demuxer for WebSocket sources, which reach both backends
        # through the local relay rather than being dialed directly.
        self._ws_hub = ws_hub
        self._stream: _AiortcStream | _Go2RtcStream
        self._set_backend(backend)

    def _set_backend(self, backend: str) -> None:
        if backend == "aiortc":
            self._stream = _AiortcStream(self._streams, self._logger, self._ws_hub)
        elif backend == "go2rtc":
            self._stream = _Go2RtcStream(self._streams, self._logger, self._ws_hub)
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

    def packet_source(self, stream_id: str, camera_url: str) -> SourceReader:
        """A reader for one source, resolved when a subscriber first attaches.

        Bound late on purpose: the hub is told which sources it serves before
        the backend swap that a config change may also carry, and only aiortc
        holds a connection in this process for anything to read.
        """

        async def reader() -> None:
            stream = self._stream
            if not isinstance(stream, _AiortcStream):
                raise RuntimeError(
                    f"camera stream {stream_id!r} is not held in this process"
                )
            await stream.packet_source(stream_id, camera_url)()

        return reader

    async def create_answer(
        self, offer: RTCSessionDescription, stream_id: str | None = None
    ) -> RTCSessionDescription:
        return await self._stream.create_answer(offer, stream_id)

    async def close(self) -> None:
        await self._stream.close()
