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
from Recorder import scrub_credentials
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


# How the live path carries RTP. TCP interleaves it inside the RTSP connection
# that is already open; UDP gives it its own datagrams.
RTSP_TRANSPORTS = ("tcp", "udp")
DEFAULT_RTSP_TRANSPORT = RTSP_TRANSPORTS[0]


# How long a held camera connection may go without a packet before it is taken
# to be dead. A camera that is unplugged, loses its link, or is quietly dropped
# by a router usually leaves its connection open and simply stops sending:
# ffmpeg keeps waiting, the peer stays "connected", and the renderer holds the
# last frame it decoded. Nothing below this notices, so the silence is timed
# here and the connection torn down, which is what turns a frozen picture into
# a reconnect.
STALL_TIMEOUT_S = 3.0
STALL_CHECK_INTERVAL_S = 1.0


def rtsp_open_options(
    timeout_us: str, transport: str = DEFAULT_RTSP_TRANSPORT
) -> dict[str, str]:
    """ffmpeg input options for dialing a camera's RTSP service.

    TCP by default. UDP is the lower-latency transport and was what this used,
    but it is also the one a VPN or a restrictive network drops outright, and a
    camera reached that way answers the RTSP handshake and then never delivers
    a packet -- which looks like a broken camera, not a blocked port. TCP also
    spares the shared connection the loss artifacts every reader would see,
    since one dial feeds both the peers and the recorder. The operator can ask
    for UDP where the network allows it and the latency is worth having.
    """
    return {
        "rtsp_transport": transport,
        "fflags": "nobuffer",
        "flags": "low_delay",
        "timeout": timeout_us,
    }


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
        # Drains this connection for as long as it lives: it feeds the relay
        # endpoint's buffer, so a recording started later begins at a keyframe,
        # and it is what times the stall watchdog below.
        self.tap: asyncio.Task | None = None
        # Watches the clock below and drops this connection when it stops.
        self.watchdog: asyncio.Task | None = None
        # Set when the camera stops being readable, so the hub can retry.
        self.ended = asyncio.Event()
        # The teardown, once it has been asked for. Every holder waits on this
        # same one rather than starting another.
        self._closing: asyncio.Future | None = None
        # Loop time of the last packet off this connection, seeded at creation
        # so a camera that never sends one is dropped on the same deadline as
        # one that stops halfway through.
        self.last_packet_at = asyncio.get_running_loop().time()

    def track(self) -> Any:
        """A proxy track for one more consumer of this connection."""
        return self.relay.subscribe(self.source)

    async def close(self) -> None:
        """Release this camera connection, once, and off the event loop."""
        if self._closing is None:
            _end_tracks(self.player)
            self._closing = utils.run_detached(
                _close_player,
                self.player,
                thread_name=f"camera-stop-{self.stream_id}",
            )
        # Shielded: every holder waits on this one teardown, so one of them
        # being cancelled must not cancel it for the rest.
        await asyncio.shield(self._closing)


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


# Teardowns nothing is waiting for, held so the loop cannot collect one
# mid-flight.
_DISCARDED: set[asyncio.Future] = set()


def _discard_dialed_player(dial: asyncio.Future) -> None:
    """Tear down a player that finished opening after we stopped waiting.

    This lands while the backend is shutting down, so the teardown is queued
    rather than waited for. If the loop has already gone, so has the process,
    and the camera session goes with it.
    """
    if dial.cancelled() or dial.exception() is not None:
        return
    with suppress(RuntimeError):
        discard = asyncio.ensure_future(_stop_player(dial.result()))
        _DISCARDED.add(discard)
        discard.add_done_callback(_DISCARDED.discard)


async def _cancel_task(task: asyncio.Task | None) -> None:
    """Stop one of a connection's background tasks and wait for it to end.

    A task that asks for its own connection to be dropped ends up here asking
    to be cancelled, which would abandon the teardown it is in the middle of,
    so the task doing the asking is left to finish on its own.
    """
    if task is None or task is asyncio.current_task():
        return
    task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task


def _end_tracks(player: MediaPlayer) -> None:
    """Announce that a player's tracks have ended. Loop thread only.

    ``stop()`` does two things: it announces the end, which reaches the event
    loop, and it tears the player down, which blocks. They have to happen on
    different threads, so the announcement is made here first -- announcing
    twice is a no-op, which is what lets the blocking half repeat it from a
    thread. Everything reading this connection sees it stop now rather than
    when the camera finally lets go.
    """
    for track in (player.video, player.audio):
        if track is not None:
            MediaStreamTrack.stop(track)


def _close_player(player: MediaPlayer) -> None:
    """Release the camera connection behind a player. Blocking: thread only.

    aiortc stops a player by joining its worker thread and then closing the
    container. That thread is usually sitting in ``demux()`` on the camera's
    socket, and on a camera that has stopped sending it stays there until
    ffmpeg's own read timeout expires -- seconds, and it is exactly the camera
    that stopped sending whose player we are most often stopping. Closing the
    container then talks to the camera as well (RTSP TEARDOWN).

    Run on the event loop, that stops the 50 Hz command sender, the telemetry
    receiver, the PTZ deadman and the control socket for the whole of it, which
    is how a camera reconnect became the robot going unresponsive.
    """
    if player.video is not None:
        player.video.stop()
    if player.audio is not None:
        player.audio.stop()


async def _stop_player(player: MediaPlayer | None) -> None:
    """Release a connection nothing has taken a hold on, off the loop."""
    if player is None:
        return
    _end_tracks(player)
    await utils.run_detached(_close_player, player, thread_name="camera-stop")


class _AiortcStream:
    """Serve WebRTC peers from one shared connection per camera.

    One connection, not one per viewer: peers take a proxy of it, and the relay
    takes another, so a recording reads the session the live view already holds
    rather than asking the camera for a second one. H.264 is forwarded exactly
    as the camera sent it -- no decode, no encode, and in a form a recording can
    stream-copy. MJPEG still has to be transcoded, because no browser will take
    it over WebRTC.
    """

    # Microseconds. ffmpeg waits ~7s (or forever, on a source that accepts the
    # connection then goes quiet) before giving up on a dial, so bound it. This
    # also covers the relayed WebSocket path: its format is cached per url, so
    # a source that has gone offline skips detect_format's own probe timeout
    # and goes straight into a MediaPlayer() open that would otherwise hang
    # forever on a relay body that never delivers a byte.
    CAMERA_OPEN_TIMEOUT_US = "5000000"

    def __init__(
        self,
        streams: CameraStreams,
        logger: logging.Logger,
        ws_hub: CameraWebSocketHub | None = None,
        rtsp_transport: str = DEFAULT_RTSP_TRANSPORT,
    ) -> None:
        self._streams: dict[str, str] = dict(streams)
        self._logger = logger
        self._ws_hub = ws_hub
        # Fixed for the life of this backend object: a transport change closes
        # every connection, so WebRTCStream rebuilds rather than mutating it.
        self._rtsp_transport = rtsp_transport
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
                    "timeout": self.CAMERA_OPEN_TIMEOUT_US,
                },
            )
        return MediaPlayer(
            camera_url,
            format="rtsp",
            options=rtsp_open_options(self.CAMERA_OPEN_TIMEOUT_US, self._rtsp_transport),
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
        self,
        offer: RTCSessionDescription,
        stream_id: str | None = None,
        restart: bool = False,
    ) -> RTCSessionDescription:
        async with self._lock:
            resolved_id = _resolve_stream_id(self._streams, stream_id)
            camera_url = self._streams[resolved_id]
            if resolved_id in self._dialing:
                raise RuntimeError(
                    f"camera stream {resolved_id!r} is already being opened"
                )
            self._dialing.add(resolved_id)

        if restart:
            # The renderer saw the picture stop. Whatever is held for this
            # source is not delivering, so it goes before the offer is
            # answered; otherwise this peer would simply join the dead
            # connection and freeze on the same frame.
            held = self._shared.get(resolved_id)
            if held is not None:
                await self._drop(held, "the renderer asked for a fresh connection")

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
                await _stop_player(player)
                # aiortc hands out a video track for h264 and vp8 only when
                # it is not decoding, and it is not decoding because this one
                # connection feeds every peer and the recorder. An H.265
                # camera reaches here having connected perfectly.
                raise RuntimeError(
                    f"camera source {stream_id!r} has no H.264 video track "
                    f"(an H.265 camera has to be set to H.264): "
                    f"{scrub_credentials(camera_url)}"
                )

            shared = _SharedPlayer(
                stream_id, camera_url, player, decoded=input_format is not None
            )
            shared.holders = 1
            shared.tap = asyncio.create_task(
                self._tap(shared), name=f"camera-tap-{stream_id}"
            )
            shared.watchdog = asyncio.create_task(
                self._watch(shared), name=f"camera-watchdog-{stream_id}"
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
            tap, shared.tap = shared.tap, None
            watchdog, shared.watchdog = shared.watchdog, None
            await _cancel_task(tap)
            await _cancel_task(watchdog)
            await shared.close()

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
        """Drain one camera's packets for as long as its connection is held.

        Two jobs, one read: it feeds the relay for the sources the hub serves,
        and it stamps every packet for the watchdog below. It runs for every
        source rather than only the relayed ones, because a connection nobody
        is pulling from cannot be told apart from one that has gone quiet.
        """
        loop = asyncio.get_running_loop()
        # MJPEG arrives decoded and has no bytestream to republish; the hub
        # serves a source only when something in this process holds it.
        relayed = (
            not shared.decoded
            and self._ws_hub is not None
            and self._ws_hub.has_stream(shared.stream_id)
        )
        track = shared.track()
        try:
            while True:
                packet = await track.recv()
                shared.last_packet_at = loop.time()
                if relayed:
                    assert self._ws_hub is not None
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

    async def _watch(self, shared: _SharedPlayer) -> None:
        """Drop a held connection once it stops delivering video.

        The tap ending is one way a camera goes; the other, and the common one,
        is that it keeps its connection open and says nothing. Both end here,
        because a viewer cannot tell them apart -- either way the picture stops
        and the connection has to be dialed again.
        """
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(STALL_CHECK_INTERVAL_S)
            if shared.ended.is_set():
                await self._drop(shared, "the camera connection ended")
                return
            idle = loop.time() - shared.last_packet_at
            if idle >= STALL_TIMEOUT_S:
                await self._drop(shared, f"no video for {idle:.1f}s")
                return

    async def _drop(self, shared: _SharedPlayer, reason: str) -> None:
        """Tear down one camera connection and every peer reading it.

        Closing the peers is the point. A renderer whose peer stays up has no
        way to tell a quiet camera from a frozen one, so it would sit on its
        last frame; dropped this way it sees the connection close, says it is
        connecting, and offers again -- and that offer dials the camera afresh,
        because the dead connection is no longer here for anything to join.
        """
        async with self._lock:
            peers = [
                peer
                for peer, (_, _, session) in self._sessions.items()
                if session is shared
            ]

        lock = self._acquiring.setdefault(shared.stream_id, asyncio.Lock())
        async with lock:
            held = self._shared.get(shared.stream_id) is shared
            if held:
                del self._shared[shared.stream_id]
            # Nothing new may join it, whichever way it went.
            shared.url = ""
        if held:
            self._logger.warning(
                "Camera %s dropped: %s; reconnecting", shared.stream_id, reason
            )
        # Started before the peers are closed, so the tap is already unblocked
        # when the last hold goes; awaited with them, so a camera that takes its
        # time being torn down does not hold up the peers' half of it -- the
        # renderer cannot start reconnecting until its peer is closed.
        await asyncio.gather(shared.close(), *(self.close_peer(peer) for peer in peers))

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
            # The URL changed; drop the stale source before re-adding so
            # go2rtc does not keep both.
            await self._forget_stream(stream_id, "RTSP stream refresh")
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
            await self._forget_stream(stream_id, "RTSP stream removal")

    async def _forget_stream(self, stream_id: str, phase: str) -> None:
        """Drop one source from the running go2rtc, if it has it."""
        if self._process is None or stream_id not in self._registered:
            return
        with suppress(OSError, SocketTimeout):
            await self._api_request(
                "DELETE", f"/api/streams?src={stream_id}", phase=phase
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
        self,
        offer: RTCSessionDescription,
        stream_id: str | None = None,
        restart: bool = False,
    ) -> RTCSessionDescription:
        async with self._lock:
            resolved_id = _resolve_stream_id(self._streams, stream_id)
            if restart:
                # go2rtc holds the camera in a child process, so the only way
                # to make it dial again is to take the source away from it.
                # Dropping the registration is enough: the sync below puts it
                # back, and go2rtc opens the camera for the first consumer.
                await self._forget_stream(resolved_id, "camera restart")
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
        rtsp_transport: str = DEFAULT_RTSP_TRANSPORT,
    ) -> None:
        self._logger = logger
        self._backend = backend
        self._rtsp_transport = rtsp_transport
        self._streams: CameraStreams = tuple(streams)
        # Resolves the demuxer for WebSocket sources, which reach both backends
        # through the local relay rather than being dialed directly.
        self._ws_hub = ws_hub
        self._stream: _AiortcStream | _Go2RtcStream
        self._set_backend(backend)

    def _set_backend(self, backend: str) -> None:
        if backend == "aiortc":
            self._stream = _AiortcStream(
                self._streams, self._logger, self._ws_hub, self._rtsp_transport
            )
        elif backend == "go2rtc":
            self._stream = _Go2RtcStream(self._streams, self._logger, self._ws_hub)
        else:
            raise ValueError("camera_backend must be 'go2rtc' or 'aiortc'")

    async def update_config(
        self,
        streams: CameraStreams,
        backend: str,
        rtsp_transport: str = DEFAULT_RTSP_TRANSPORT,
    ) -> bool:
        """Apply a camera configuration. True if every connection was replaced.

        A backend change replaces all of them: the new backend holds nothing.
        A transport change does too, but only under aiortc -- that is the
        backend that dials RTSP in this process, and a transport is chosen when
        a connection is opened. go2rtc dials inside a child process and picks
        its own transport, so rebuilding it for this setting would stop, start
        and re-dial every camera to change something it never reads, which is
        seconds of black screen bought for nothing.
        """
        streams = tuple(streams)
        rebuild = backend != self._backend or (
            backend == "aiortc" and rtsp_transport != self._rtsp_transport
        )
        # Recorded either way, so a later switch to aiortc opens its dials with
        # the transport the operator chose while go2rtc was running.
        self._rtsp_transport = rtsp_transport
        self._streams = streams
        if not rebuild:
            await self._stream.update_streams(streams)
            return False

        await self.close()
        self._backend = backend
        self._set_backend(backend)
        if streams:
            await self._stream.update_streams(streams)
        return True

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

    @property
    def stream_ids(self) -> tuple[str, ...]:
        """The ids a ``?src=`` may name, for reporting one that misses."""
        return tuple(stream_id for stream_id, _ in self._streams)

    async def create_answer(
        self,
        offer: RTCSessionDescription,
        stream_id: str | None = None,
        restart: bool = False,
    ) -> RTCSessionDescription:
        """Answer one receive-only offer.

        ``restart`` is the renderer reporting that this source stopped
        delivering: the connection held for it is thrown away and the camera
        dialed again, instead of the new peer joining a feed that has already
        stopped.
        """
        return await self._stream.create_answer(offer, stream_id, restart)

    async def close(self) -> None:
        await self._stream.close()
