"""Re-serve direct camera WebSocket sources as plain HTTP byte streams.

Some cameras expose video over a bespoke WebSocket: the client sends the text
``PlayStream2`` and the camera answers with binary messages carrying a raw
H.264 (Annex-B) or MJPEG bytestream, interleaved with text status frames.

Neither go2rtc nor ffmpeg can read a WebSocket, so the backend bridges the two:
this hub holds exactly one connection per camera and fans its payloads out to
any number of subscribers. ``GET /camera/<id>/stream`` is one subscriber; the
camera backend that turns the source into WebRTC reads that endpoint. Holding a
single upstream connection matters -- this firmware often serves one session at
a time, so a second dial would be refused or would evict the first.

The hub also keeps the payloads since each camera's last keyframe, so a
subscriber that asks for them starts decoding at a picture that is already on
screen instead of at the camera's next keyframe. Recording is what needs this;
see ``PrerollBuffer``.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator
from urllib.parse import urlparse

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

# The text frame the camera expects before it starts sending video.
PLAY_COMMAND = "PlayStream2"

# The renderer used to reach these cameras directly; now only the backend does,
# and it serves them back to the renderer's camera pipeline from here. The
# relay address is the backend's own, so it has to follow the port uvicorn was
# actually started on rather than assuming the default.
RELAY_HOST = "127.0.0.1"
RELAY_PORT = int(os.environ.get("APP_BACKEND_PORT", "8000"))

# A camera that accepts the connection then goes quiet must not hang a probe or
# wedge a live source, so both reads are bounded.
FIRST_PAYLOAD_TIMEOUT_S = 10.0
RECV_TIMEOUT_S = 10.0
CONNECT_TIMEOUT_S = 5.0
CLOSE_TIMEOUT_S = 2.0

# Reconnect backoff after the camera drops, in seconds.
RECONNECT_BASE_S = 1.0
RECONNECT_MAX_S = 15.0

# Payloads held for one subscriber before it is considered too slow. These are
# whole camera messages, so a handful is already a fraction of a second.
SUBSCRIBER_QUEUE_SIZE = 60

# How much of one GOP the preroll buffer will hold, per source. A stream copy
# can only begin at a keyframe, and on an IP camera those are commonly ten
# seconds apart, so the buffer is sized for a GOP that long at a generous
# bitrate and frame rate.
#
# A GOP that outgrows either cap is dropped rather than trimmed: trimming would
# leave a headless GOP, and splicing a consumer into the middle of one corrupts
# everything up to the next parameter set -- the same reason _publish cuts a
# slow subscriber off instead of dropping single payloads. The source then
# behaves as it did before there was a buffer.
PREROLL_MAX_PAYLOADS = 900
PREROLL_MAX_BYTES = 16 * 1024 * 1024

# The Annex-B start code. The four-byte form is this same code behind one byte
# of padding, so finding this finds both.
START_CODE = b"\x00\x00\x01"

# A JPEG start of image. Every MJPEG frame stands alone, so each one is its own
# keyframe.
JPEG_SOI = b"\xff\xd8"

# The H.264 NAL unit types this module has to tell apart.
H264_NAL_IDR = 5
H264_NAL_SPS = 7
# SEI, SPS, PPS and the access unit delimiter: headers, carrying no picture.
H264_NON_PICTURE_NALS = frozenset({6, 7, 8, 9})


def is_websocket_url(url: str) -> bool:
    """Whether a configured camera source is served over WebSocket."""
    return urlparse(url).scheme in {"ws", "wss"}


def relay_url(
    stream_id: str,
    host: str = RELAY_HOST,
    port: int = RELAY_PORT,
    preroll: bool = False,
) -> str:
    """The local HTTP endpoint that re-serves one WebSocket camera.

    ``preroll`` asks the relay to replay the payloads since the camera's last
    keyframe before the live ones. One url serves both the live path and the
    recorder, so which of them wants it has to be said here: recording does,
    the live view does not.
    """
    url = f"http://{host}:{port}/camera/{stream_id}/stream"
    return f"{url}?preroll=1" if preroll else url


def detect_payload_format(payload: bytes) -> str | None:
    """Identify a camera payload as ``mjpeg`` or ``h264``.

    A raw bytestream advertises no container, so ffmpeg has to be told which
    demuxer to use and guesses badly if it is not. ``stream_ws.py`` found this
    camera family emitting JPEG on some paths and H.264 on others, and pointing
    the h264 demuxer at JPEG yields an empty file rather than an error, so the
    format is sniffed from the first payload instead of assumed.
    """
    if payload[:2] == JPEG_SOI:
        return "mjpeg"
    if payload[:3] == START_CODE or payload[:4] == b"\x00" + START_CODE:
        return "h264"
    return None


def should_forward(message: str | bytes) -> bool:
    """Whether a camera message carries video.

    The camera interleaves text status frames such as
    ``<playback_status status="ok" />`` with the video payloads.
    """
    return isinstance(message, (bytes, bytearray)) and len(message) > 0


def reconnect_delay(attempt: int) -> float:
    """Backoff before redialing a camera that dropped."""
    return min(RECONNECT_BASE_S * (2 ** max(attempt - 1, 0)), RECONNECT_MAX_S)


def annexb_nal_types(payload: bytes) -> tuple[int, ...]:
    """The H.264 NAL unit types in one Annex-B payload, in order.

    A start code cannot occur inside a NAL's own body -- that is what the
    emulation prevention byte exists to guarantee -- so scanning for it is
    exact rather than a heuristic.
    """
    types: list[int] = []
    index = payload.find(START_CODE)
    while index != -1:
        header = index + len(START_CODE)
        if header >= len(payload):
            break
        types.append(payload[header] & 0x1F)
        index = payload.find(START_CODE, header)
    return tuple(types)


def carries_picture(payload: bytes) -> bool:
    """Whether a payload carries coded picture data rather than only headers."""
    if payload[:2] == JPEG_SOI:
        return True
    return any(
        nal_type not in H264_NON_PICTURE_NALS for nal_type in annexb_nal_types(payload)
    )


class PrerollBuffer:
    """The payloads since one camera's most recent keyframe.

    A consumer handed these before any live payload has a decodable picture
    immediately. Without them it must wait for the camera's next keyframe,
    because a stream copy cannot keep the packets before one -- nothing can
    decode them without their reference frame. That wait is what made a
    recording open several seconds after the operator pressed record.

    The buffer therefore reaches back to before the moment recording started,
    never after it. Reaching back is the price of starting on a keyframe, and
    it is the right way round: a recording that opens slightly early keeps
    everything that mattered, one that opens late has already lost it.
    """

    def __init__(self) -> None:
        self._payloads: list[bytes] = []
        self._size = 0
        # Whether the buffer starts at a keyframe. Until it does there is
        # nothing worth replaying: a decoder joining mid-GOP shows nothing
        # until the next one anyway.
        self._primed = False
        # Whether a coded picture has arrived since it did. Parameter sets
        # often reach the hub as payloads of their own just ahead of the IDR
        # they describe, and those belong to the keyframe that follows rather
        # than to the GOP that came before it.
        self._pictured = False
        # Sticky, and deliberately not cleared by reset(): one report per
        # source is the useful amount for a camera whose GOP never fits.
        self.overflowed = False

    def __len__(self) -> int:
        return len(self._payloads)

    def reset(self) -> None:
        """Forget everything, because the bytestream is about to jump."""
        self._payloads = []
        self._size = 0
        self._primed = False
        self._pictured = False

    def add(self, payload: bytes) -> None:
        """Take one camera payload."""
        if self._is_head(payload):
            self._payloads = [payload]
            self._size = len(payload)
            self._primed = True
            self._pictured = carries_picture(payload)
            return
        if not self._primed:
            return
        if (
            len(self._payloads) >= PREROLL_MAX_PAYLOADS
            or self._size + len(payload) > PREROLL_MAX_BYTES
        ):
            self.reset()
            self.overflowed = True
            return
        self._payloads.append(payload)
        self._size += len(payload)
        self._pictured = self._pictured or carries_picture(payload)

    def snapshot(self) -> tuple[bytes, ...]:
        """The payloads to replay to a new subscriber, oldest first."""
        return tuple(self._payloads) if self._primed else ()

    def _is_head(self, payload: bytes) -> bool:
        """Whether this payload starts a GOP the buffer should restart on."""
        if payload[:2] == JPEG_SOI:
            return True
        types = annexb_nal_types(payload)
        if not types:
            return False
        if types[0] == H264_NAL_SPS:
            return True
        if H264_NAL_IDR in types:
            # Keep the parameter sets already waiting for this very keyframe;
            # start afresh when what is buffered is the GOP before it, or when
            # the stream was joined partway through one.
            return self._pictured or not self._primed
        return False


def _close_subscriber(subscriber: "_Subscriber") -> None:
    """Signal end-of-stream, dropping the sentinel if the backlog is already full."""
    try:
        subscriber.queue.put_nowait(None)
    except asyncio.QueueFull:
        pass


class _Subscriber:
    """One consumer of a camera source, with its own bounded backlog."""

    def __init__(self, preroll: tuple[bytes, ...] = ()) -> None:
        # The replayed GOP sits on top of the live backlog rather than eating
        # into it, so priming a subscriber can never leave it looking slow.
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue(
            maxsize=SUBSCRIBER_QUEUE_SIZE + len(preroll)
        )
        self.overrun = False
        for payload in preroll:
            self.queue.put_nowait(payload)


class _Source:
    """One camera WebSocket, its reader task, and its subscribers."""

    def __init__(self, stream_id: str, url: str) -> None:
        self.stream_id = stream_id
        self.url = url
        self.subscribers: set[_Subscriber] = set()
        self.task: asyncio.Task | None = None
        self.input_format: str | None = None
        self.preroll = PrerollBuffer()


class CameraWebSocketHub:
    """Own one connection per WebSocket camera and fan it out to subscribers."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._sources: dict[str, _Source] = {}
        # Format is a property of the camera, not of a connection, so it
        # survives reconnects and is shared by every subscriber.
        self._formats: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def has_stream(self, stream_id: str) -> bool:
        """Whether a WebSocket source with this id is configured."""
        return stream_id in self._sources

    async def update_streams(self, streams: tuple[tuple[str, str], ...]) -> None:
        """Adopt the configured WebSocket sources, dropping the ones that left."""
        wanted = {
            stream_id: url for stream_id, url in streams if is_websocket_url(url)
        }
        async with self._lock:
            stale = [
                source
                for stream_id, source in self._sources.items()
                if wanted.get(stream_id) != source.url
            ]
            for source in stale:
                self._sources.pop(source.stream_id, None)
                self._formats.pop(source.url, None)
            for stream_id, url in wanted.items():
                if stream_id not in self._sources:
                    self._sources[stream_id] = _Source(stream_id, url)

        for source in stale:
            await self._stop(source)

    async def detect_format(self, stream_id: str, url: str) -> str:
        """Return ``h264`` or ``mjpeg`` for one camera, probing it if needed.

        The camera backend needs the demuxer name when it registers the source,
        which is before any subscriber exists, so this opens its own short
        connection. The result is cached per url and outlives reconnects, so a
        camera is probed once per configuration rather than once per dial.
        """
        cached = self._formats.get(url)
        if cached is not None:
            return cached

        payload = await self._probe(url)
        detected = detect_payload_format(payload) if payload else None
        if detected is None:
            # Either the probe never reached the camera, or it sent something
            # with no recognizable start code. Guess H.264 -- much the more
            # likely of the two -- rather than refusing to bring the source up,
            # but say which case this was, because "unreachable" and "speaks an
            # unexpected format" call for very different fixes.
            self._logger.warning(
                "Camera %s: %s; assuming H.264",
                stream_id,
                "could not be reached to detect its format"
                if payload is None
                else "sent an unrecognized payload",
            )
            detected = "h264"
        self._formats[url] = detected
        self._logger.info("Camera %s stream format detected as %s", stream_id, detected)
        return detected

    async def _probe(self, url: str) -> bytes | None:
        try:
            async with connect(
                url,
                max_size=None,
                ping_interval=None,
                open_timeout=CONNECT_TIMEOUT_S,
                close_timeout=CLOSE_TIMEOUT_S,
            ) as socket:
                await socket.send(PLAY_COMMAND)
                deadline = asyncio.get_running_loop().time() + FIRST_PAYLOAD_TIMEOUT_S
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        return None
                    message = await asyncio.wait_for(socket.recv(), remaining)
                    if should_forward(message):
                        return bytes(message)
        except (OSError, WebSocketException, asyncio.TimeoutError) as error:
            self._logger.warning("Camera probe failed for %s: %s", url, error)
            return None

    @asynccontextmanager
    async def subscribe(
        self, stream_id: str, preroll: bool = False
    ) -> AsyncIterator[AsyncIterator[bytes]]:
        """Yield the camera's payloads for as long as the caller stays attached.

        The upstream connection is opened for the first subscriber and closed
        after the last one leaves, so a camera nobody is watching or recording
        costs nothing.

        ``preroll`` replays the payloads since the camera's last keyframe ahead
        of the live ones, so the consumer has a decodable picture at once
        instead of after the camera's next keyframe. Recording wants that; the
        live view does not, because it would open on video that is already old
        and then have to race back to the present.
        """
        async with self._lock:
            source = self._sources.get(stream_id)
            if source is None:
                raise KeyError(stream_id)
            # Nothing may await between reading the buffer and joining the
            # fan-out: the pump publishes from another task, and a payload it
            # sent in between would reach this subscriber ahead of the replay
            # and splice its bytestream.
            subscriber = _Subscriber(source.preroll.snapshot() if preroll else ())
            source.subscribers.add(subscriber)
            if source.task is None:
                source.task = asyncio.create_task(
                    self._run(source), name=f"camera-ws-{stream_id}"
                )

        try:
            yield self._drain(source, subscriber)
        finally:
            async with self._lock:
                source.subscribers.discard(subscriber)
                idle = not source.subscribers
            if idle:
                await self._stop(source)

    async def _drain(
        self, source: _Source, subscriber: _Subscriber
    ) -> AsyncIterator[bytes]:
        while True:
            payload = await subscriber.queue.get()
            if payload is None:
                return
            yield payload

    async def _run(self, source: _Source) -> None:
        """Hold the camera connection open, redialing until nobody is left."""
        attempt = 0
        while True:
            try:
                await self._pump(source)
                attempt = 0
            except asyncio.CancelledError:
                raise
            except (OSError, WebSocketException, asyncio.TimeoutError) as error:
                attempt += 1
                self._logger.warning(
                    "Camera %s stream ended (%s); reconnecting", source.stream_id, error
                )
            finally:
                # Whatever was buffered belongs to a connection that has ended.
                # Replaying it in front of the next one would splice a
                # subscriber across the gap between them.
                source.preroll.reset()
            await asyncio.sleep(reconnect_delay(attempt))

    async def _pump(self, source: _Source) -> None:
        async with connect(
            source.url,
            # The default 1 MiB frame cap is smaller than a high-bitrate
            # keyframe, and exceeding it kills the connection mid-stream.
            max_size=None,
            # Bespoke camera firmware may never answer a pong. The bounded
            # recv below is the liveness check instead, and it also catches a
            # camera that holds the socket open but stops sending video.
            ping_interval=None,
            open_timeout=CONNECT_TIMEOUT_S,
            close_timeout=CLOSE_TIMEOUT_S,
        ) as socket:
            await socket.send(PLAY_COMMAND)
            self._logger.info("Camera %s stream connected", source.stream_id)
            while True:
                message = await asyncio.wait_for(socket.recv(), RECV_TIMEOUT_S)
                if not should_forward(message):
                    continue
                self._publish(source, bytes(message))

    def _publish(self, source: _Source, payload: bytes) -> None:
        overflowed = source.preroll.overflowed
        source.preroll.add(payload)
        if source.preroll.overflowed and not overflowed:
            self._logger.warning(
                "Camera %s sends keyframes too far apart to buffer; "
                "recordings of it will start at its next one",
                source.stream_id,
            )

        for subscriber in tuple(source.subscribers):
            if subscriber.overrun:
                continue
            try:
                subscriber.queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Never drop a single payload to make room: these are Annex-B
                # bytestreams consumed with `-c copy`, so a gap splices mid-NAL
                # and corrupts everything until the next parameter set. Cut the
                # slow subscriber off instead and let it reconnect cleanly.
                subscriber.overrun = True
                self._logger.warning(
                    "Camera %s subscriber fell behind; disconnecting it",
                    source.stream_id,
                )
                _close_subscriber(subscriber)

    async def _stop(self, source: _Source) -> None:
        task = source.task
        source.task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        for subscriber in tuple(source.subscribers):
            _close_subscriber(subscriber)

    async def close(self) -> None:
        async with self._lock:
            sources = tuple(self._sources.values())
            self._sources.clear()
            self._formats.clear()
        for source in sources:
            await self._stop(source)
