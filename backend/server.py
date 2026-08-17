import asyncio
import json
import logging
import socket
import threading
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, AsyncIterator
from urllib.parse import urlparse
import os

import cv2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import StreamingResponse
import settings as settings_module
import utils

LOGGER = logging.getLogger(__name__)
UDP_SEND_HZ = 50
RTSP_RECONNECT_DELAY_S = 1.0
JPEG_QUALITY = 60
MJPEG_BOUNDARY = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "packets-schema.json"
with SCHEMA_PATH.open(encoding="utf-8") as schema_file:
    PACKET_SCHEMA = json.load(schema_file)


@dataclass
class RuntimeState:
    config: settings_module.Settings
    current_packet: dict[str, Any]
    packet_payload: bytes
    udp_enabled: bool = False
    connected_clients: int = 0


def encode_packet(packet: dict[str, Any]) -> bytes:
    return utils.encode_binary_packet(packet, PACKET_SCHEMA)


default_packet = utils.generate_default_state(PACKET_SCHEMA)
runtime = RuntimeState(
    config=settings_module.Settings(
        udp_ip="127.0.0.1",
        udp_port=8888,
        camera_url="rtsp://admin:password@127.0.0.1:554/stream",
    ),
    current_packet=default_packet,
    packet_payload=encode_packet(default_packet),
)


os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;udp|fflags;nobuffer|flags;low_delay"
)


class MjpegStream:
    """Capture and encode one camera source for all connected HTTP clients."""

    def __init__(self, camera_url: str) -> None:
        self._camera_url = camera_url
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame: bytes | None = None
        self._sequence = 0
        self._subscribers = 0

    def update_url(self, camera_url: str) -> None:
        with self._condition:
            self._camera_url = camera_url

    async def frames(self) -> AsyncIterator[bytes]:
        with self._condition:
            self._subscribers += 1
            self._stop_event.clear()
            self._start_worker_locked()
            last_sequence = self._sequence

        try:
            while not self._stop_event.is_set():
                # Lock the condition to safely read the current frame and sequence number
                with self._condition:
                    current_sequence = self._sequence
                    frame = self._frame

                if current_sequence != last_sequence:
                    last_sequence = current_sequence
                    if frame is not None:
                        yield MJPEG_BOUNDARY + frame + b"\r\n"
                else:
                    # Bring back control to the Uvicorn Event Loop.
                    # This allows Uvicorn to cancel this task instantly on Ctrl-C.
                    await asyncio.sleep(0.02)  # ~50 FPS polling rate
        finally:
            with self._condition:
                self._subscribers -= 1
                if self._subscribers == 0:
                    self._stop_event.set()
                    self._condition.notify_all()

    def close(self) -> None:
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()

        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)

    def _start_worker_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._thread = threading.Thread(
            target=self._capture_loop,
            name="camera-capture",
            daemon=True,
        )
        self._thread.start()

    def _capture_loop(self) -> None:
        capture: cv2.VideoCapture | None = None
        capture_url = ""

        try:
            while not self._stop_event.is_set():
                with self._condition:
                    configured_url = self._camera_url

                if not configured_url:
                    if capture is not None:
                        capture.release()
                        capture = None
                    capture_url = ""
                    self._stop_event.wait(RTSP_RECONNECT_DELAY_S)
                    continue

                if capture is None or configured_url != capture_url:
                    if capture is not None:
                        capture.release()
                    capture_url = configured_url

                    capture = cv2.VideoCapture(
                        capture_url,
                        cv2.CAP_FFMPEG,
                        params=[
                            cv2.CAP_PROP_HW_ACCELERATION,
                            cv2.VIDEO_ACCELERATION_ANY,
                        ],
                    )

                    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                    if not capture.isOpened():
                        LOGGER.warning("Unable to open camera stream; retrying")
                        capture.release()
                        capture = None
                        self._stop_event.wait(RTSP_RECONNECT_DELAY_S)
                        continue

                # Rapidly drain the buffer to ensure we only decode the absolute latest frame
                success = capture.grab()
                if not success:
                    LOGGER.warning("Camera frame grab failed; reconnecting")
                    capture.release()
                    capture = None
                    self._stop_event.wait(RTSP_RECONNECT_DELAY_S)
                    continue

                # Retrieve the actual image data from the last grab
                _, frame = capture.retrieve()

                # Reduce resolution
                # frame = cv2.resize(frame, (640, 480))

                encoded, buffer = cv2.imencode(
                    ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
                )
                if not encoded:
                    LOGGER.warning("JPEG frame encoding failed")
                    continue

                with self._condition:
                    self._frame = buffer.tobytes()
                    self._sequence += 1
                    self._condition.notify_all()
        finally:
            if capture is not None:
                capture.release()

            with self._condition:
                self._thread = None
                if self._subscribers > 0 and not self._stop_event.is_set():
                    self._start_worker_locked()
                self._condition.notify_all()


video_stream = MjpegStream(runtime.config.camera_url)


def validate_config(config: dict[str, Any]) -> tuple[str, int, str, bool]:
    allowed_keys = {"udp_host", "udp_port", "camera_url"}
    unknown_keys = set(config) - allowed_keys
    if unknown_keys:
        raise ValueError(f"Unknown config fields: {sorted(unknown_keys)}")

    udp_host_value = config.get("udp_host", runtime.config.udp_ip)
    udp_port_value = config.get("udp_port", runtime.config.udp_port)
    camera_url_value = config.get("camera_url", runtime.config.camera_url)
    if not isinstance(udp_host_value, str):
        raise ValueError("udp_host must be a string")
    if isinstance(udp_port_value, bool) or not isinstance(udp_port_value, int):
        raise ValueError("udp_port must be an integer")
    if not isinstance(camera_url_value, str):
        raise ValueError("camera_url must be a string")

    udp_host = udp_host_value.strip()
    udp_port = udp_port_value
    camera_url = camera_url_value.strip()

    udp_enabled = bool(udp_host or udp_port)
    if udp_enabled and (not udp_host or not 1 <= udp_port <= 65535):
        raise ValueError("udp_host and udp_port 1..65535 must both be set")
    parsed_camera_url = urlparse(camera_url)
    if camera_url and (
        parsed_camera_url.scheme not in {"http", "https", "rtsp"}
        or not parsed_camera_url.hostname
    ):
        raise ValueError("camera_url must be a valid HTTP, HTTPS, or RTSP URL")
    return udp_host, udp_port, camera_url, udp_enabled


async def udp_loop() -> None:
    """Send the latest controls at a stable rate while a UI is connected."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    loop = asyncio.get_running_loop()
    interval = utils.hz_to_s(UDP_SEND_HZ)
    next_send = loop.time()
    last_error_log = 0.0

    try:
        while True:
            # print(
            #     f"Clients: {runtime.connected_clients}, UDP Enabled: {runtime.udp_enabled}"
            # )

            if runtime.connected_clients > 0 and runtime.udp_enabled:
                try:
                    sock.sendto(
                        runtime.packet_payload,
                        (runtime.config.udp_ip, runtime.config.udp_port),
                    )
                except BlockingIOError:
                    # Non-blocking socket OS buffer is full, just skip this tick
                    pass
                except OSError as error:
                    now = loop.time()
                    if now - last_error_log >= 1.0:
                        LOGGER.warning(
                            "UDP send to %s:%s failed: %s",
                            runtime.config.udp_ip,
                            runtime.config.udp_port,
                            error,
                        )
                        last_error_log = now

            next_send += interval
            delay = next_send - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                next_send = loop.time()

    except asyncio.CancelledError:
        # Expected when FastAPI shuts down
        raise
    except Exception as e:
        # CRITICAL: Catch any other errors so the task doesn't die silently
        LOGGER.error("UDP loop crashed unexpectedly: %s", e)
    finally:
        sock.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    udp_task = asyncio.create_task(udp_loop(), name="udp-sender")
    try:
        yield
    finally:
        udp_task.cancel()
        with suppress(asyncio.CancelledError):
            await udp_task
        video_stream.close()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Lightweight readiness probe for the container entrypoint."""
    return {"status": "ok"}


@app.get("/stream")
async def video_feed(request: Request) -> StreamingResponse:
    async def frames_generator():
        async for frame in video_stream.frames():
            # If the Vue UI tab is closed, break the infinite loop
            if await request.is_disconnected():
                break
            yield frame

    return StreamingResponse(
        frames_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.websocket("/ws/controls")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    runtime.connected_clients += 1
    LOGGER.info(
        "UI connected; sending controls every %.2f ms to %s:%s",
        utils.hz_to_ms(UDP_SEND_HZ),
        runtime.config.udp_ip,
        runtime.config.udp_port,
    )

    try:
        while True:
            try:
                incoming_data = json.loads(await websocket.receive_text())
                if not isinstance(incoming_data, dict):
                    raise ValueError("WebSocket message must be an object")
                message_type = incoming_data.get("type")

                if message_type == "config":
                    config = incoming_data.get("config", {})
                    if not isinstance(config, dict):
                        raise ValueError("config must be an object")
                    udp_host, udp_port, camera_url, udp_enabled = validate_config(
                        config
                    )
                    runtime.config.udp_ip = udp_host
                    runtime.config.udp_port = udp_port
                    runtime.config.camera_url = camera_url
                    runtime.udp_enabled = udp_enabled
                    video_stream.update_url(camera_url)
                    print(
                        "UDP config accepted: "
                        f"enabled={udp_enabled} destination="
                        f"{udp_host}:{udp_port} clients={runtime.connected_clients}",
                        flush=True,
                    )
                elif message_type == "control":
                    packet = incoming_data.get("packet", {})
                    if not isinstance(packet, dict):
                        raise ValueError("packet must be an object")
                    utils.validate_packet_values(packet, PACKET_SCHEMA)
                    next_packet = {**runtime.current_packet, **packet}
                    runtime.packet_payload = encode_packet(next_packet)
                    runtime.current_packet = next_packet
                else:
                    raise ValueError("message type must be 'config' or 'control'")
            except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
                await websocket.send_json({"type": "error", "message": str(error)})
    except WebSocketDisconnect:
        LOGGER.info("UI disconnected")
    finally:
        runtime.connected_clients = max(0, runtime.connected_clients - 1)
        if runtime.connected_clients == 0:
            runtime.current_packet = utils.generate_default_state(PACKET_SCHEMA)
            runtime.packet_payload = encode_packet(runtime.current_packet)
