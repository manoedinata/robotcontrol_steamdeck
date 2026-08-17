import asyncio
import json
import logging
import socket
import threading
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import cv2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from jsonschema import Draft202012Validator, ValidationError

import settings as settings_module
import utils

LOGGER = logging.getLogger(__name__)
UDP_SEND_HZ = 50
RTSP_RECONNECT_DELAY_S = 1.0
JPEG_QUALITY = 75
MJPEG_BOUNDARY = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "packets-schema.json"
with SCHEMA_PATH.open(encoding="utf-8") as schema_file:
    PACKET_SCHEMA = json.load(schema_file)
PACKET_VALIDATOR = Draft202012Validator(PACKET_SCHEMA)


@dataclass
class RuntimeState:
    config: settings_module.Settings
    current_packet: dict[str, Any]
    packet_payload: bytes
    connected_clients: int = 0


def encode_packet(packet: dict[str, Any]) -> bytes:
    return json.dumps(packet, separators=(",", ":")).encode("utf-8")


default_packet = utils.generate_default_state(PACKET_SCHEMA)
runtime = RuntimeState(
    config=settings_module.Settings(
        udp_ip="127.0.0.1",
        udp_port=8888,
        rtsp_url="rtsp://admin:password@127.0.0.1:554/stream",
    ),
    current_packet=default_packet,
    packet_payload=encode_packet(default_packet),
)


class MjpegStream:
    """Capture and encode one RTSP stream for all connected HTTP clients."""

    def __init__(self, rtsp_url: str) -> None:
        self._rtsp_url = rtsp_url
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame: bytes | None = None
        self._sequence = 0
        self._subscribers = 0

    def update_url(self, rtsp_url: str) -> None:
        with self._condition:
            self._rtsp_url = rtsp_url

    def frames(self) -> Iterator[bytes]:
        with self._condition:
            self._subscribers += 1
            self._stop_event.clear()
            self._start_worker_locked()
            last_sequence = self._sequence

        try:
            while True:
                with self._condition:
                    self._condition.wait_for(
                        lambda: self._sequence != last_sequence
                        or self._stop_event.is_set()
                    )
                    if self._stop_event.is_set() and self._sequence == last_sequence:
                        return
                    frame = self._frame
                    last_sequence = self._sequence

                if frame is not None:
                    yield MJPEG_BOUNDARY + frame + b"\r\n"
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
            name="rtsp-capture",
            daemon=True,
        )
        self._thread.start()

    def _capture_loop(self) -> None:
        capture: cv2.VideoCapture | None = None
        capture_url = ""

        try:
            while not self._stop_event.is_set():
                with self._condition:
                    configured_url = self._rtsp_url

                if capture is None or configured_url != capture_url:
                    if capture is not None:
                        capture.release()
                    capture_url = configured_url
                    capture = cv2.VideoCapture(capture_url)
                    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                    if not capture.isOpened():
                        LOGGER.warning("Unable to open RTSP stream; retrying")
                        capture.release()
                        capture = None
                        self._stop_event.wait(RTSP_RECONNECT_DELAY_S)
                        continue

                success, frame = capture.read()
                if not success:
                    LOGGER.warning("RTSP frame read failed; reconnecting")
                    capture.release()
                    capture = None
                    self._stop_event.wait(RTSP_RECONNECT_DELAY_S)
                    continue

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


video_stream = MjpegStream(runtime.config.rtsp_url)


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
            if runtime.connected_clients > 0:
                try:
                    await loop.sock_sendto(
                        sock,
                        runtime.packet_payload,
                        (runtime.config.udp_ip, runtime.config.udp_port),
                    )
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


@app.get("/stream")
def video_feed() -> StreamingResponse:
    """Expose the configured RTSP source as a shared MJPEG stream."""
    return StreamingResponse(
        video_stream.frames(),
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
            incoming_data = json.loads(await websocket.receive_text())

            if "ip" in incoming_data:
                runtime.config.udp_ip = str(incoming_data.pop("ip"))
            if "port" in incoming_data:
                runtime.config.udp_port = int(incoming_data.pop("port"))
            if "rtsp_url" in incoming_data:
                runtime.config.rtsp_url = str(incoming_data.pop("rtsp_url"))
                video_stream.update_url(runtime.config.rtsp_url)

            if incoming_data:
                try:
                    PACKET_VALIDATOR.validate(incoming_data)
                except ValidationError as error:
                    LOGGER.warning("Ignored invalid control packet: %s", error.message)
                else:
                    runtime.current_packet.update(incoming_data)
                    runtime.packet_payload = encode_packet(runtime.current_packet)
    except WebSocketDisconnect:
        LOGGER.info("UI disconnected")
    finally:
        runtime.connected_clients = max(0, runtime.connected_clients - 1)
        if runtime.connected_clients == 0:
            runtime.current_packet = utils.generate_default_state(PACKET_SCHEMA)
            runtime.packet_payload = encode_packet(runtime.current_packet)
