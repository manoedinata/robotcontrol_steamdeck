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


def is_websocket_url(url: str) -> bool:
    """Whether a configured camera source is served over WebSocket."""
    return urlparse(url).scheme in {"ws", "wss"}


def relay_url(stream_id: str, host: str = RELAY_HOST, port: int = RELAY_PORT) -> str:
    """The local HTTP endpoint that re-serves one WebSocket camera."""
    return f"http://{host}:{port}/camera/{stream_id}/stream"


def detect_payload_format(payload: bytes) -> str | None:
    """Identify a camera payload as ``mjpeg`` or ``h264``.

    A raw bytestream advertises no container, so ffmpeg has to be told which
    demuxer to use and guesses badly if it is not. ``stream_ws.py`` found this
    camera family emitting JPEG on some paths and H.264 on others, and pointing
    the h264 demuxer at JPEG yields an empty file rather than an error, so the
    format is sniffed from the first payload instead of assumed.
    """
    if payload[:2] == b"\xff\xd8":
        return "mjpeg"
    if payload[:3] == b"\x00\x00\x01" or payload[:4] == b"\x00\x00\x00\x01":
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


def _close_subscriber(subscriber: "_Subscriber") -> None:
    """Signal end-of-stream, dropping the sentinel if the backlog is already full."""
    try:
        subscriber.queue.put_nowait(None)
    except asyncio.QueueFull:
        pass


class _Subscriber:
    """One consumer of a camera source, with its own bounded backlog."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue(
            maxsize=SUBSCRIBER_QUEUE_SIZE
        )
        self.overrun = False


class _Source:
    """One camera WebSocket, its reader task, and its subscribers."""

    def __init__(self, stream_id: str, url: str) -> None:
        self.stream_id = stream_id
        self.url = url
        self.subscribers: set[_Subscriber] = set()
        self.task: asyncio.Task | None = None
        self.input_format: str | None = None


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
    async def subscribe(self, stream_id: str) -> AsyncIterator[AsyncIterator[bytes]]:
        """Yield the camera's payloads for as long as the caller stays attached.

        The upstream connection is opened for the first subscriber and closed
        after the last one leaves, so a camera nobody is watching or recording
        costs nothing.
        """
        async with self._lock:
            source = self._sources.get(stream_id)
            if source is None:
                raise KeyError(stream_id)
            subscriber = _Subscriber()
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
