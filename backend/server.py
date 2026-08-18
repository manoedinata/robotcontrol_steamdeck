import asyncio
import json
import logging
import re
import socket
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aiortc import RTCSessionDescription
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
import settings as settings_module
from WebRTCStream import WebRTCStream
import utils

LOGGER = logging.getLogger(__name__)
UDP_SEND_HZ = 50
PING_INTERVAL_S = 2.0
PING_TIMEOUT_S = 1.0
PING_VALUE_PATTERN = re.compile(r"time[=<]([0-9]+(?:\.[0-9]+)?)\s*ms")

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "packets-schema.json"
with SCHEMA_PATH.open(encoding="utf-8") as schema_file:
    PACKET_SCHEMA = json.load(schema_file)


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)


@dataclass
class RuntimeState:
    config: settings_module.Settings
    current_packet: dict[str, Any]
    packet_payload: bytes
    udp_enabled: bool = False
    clients: dict[WebSocket, asyncio.Lock] = field(default_factory=dict)


def encode_packet(packet: dict[str, Any]) -> bytes:
    return utils.encode_binary_packet(packet, PACKET_SCHEMA)


default_packet = utils.generate_default_state(PACKET_SCHEMA)
runtime = RuntimeState(
    config=settings_module.Settings(
        udp_ip="127.0.0.1",
        udp_port=8888,
        udp_listen_port=8889,
        camera_url="rtsp://admin:password@127.0.0.1:554/stream",
    ),
    current_packet=default_packet,
    packet_payload=encode_packet(default_packet),
)


video_stream = WebRTCStream(runtime.config.camera_url, logger=LOGGER)


def validate_config(config: dict[str, Any]) -> tuple[str, int, int, str, bool]:
    allowed_keys = {"udp_host", "udp_port", "udp_listen_port", "camera_url"}
    unknown_keys = set(config) - allowed_keys
    if unknown_keys:
        raise ValueError(f"Unknown config fields: {sorted(unknown_keys)}")

    udp_host_value = config.get("udp_host", runtime.config.udp_ip)
    udp_port_value = config.get("udp_port", runtime.config.udp_port)
    udp_listen_port_value = config.get(
        "udp_listen_port", runtime.config.udp_listen_port
    )
    camera_url_value = config.get("camera_url", runtime.config.camera_url)
    if not isinstance(udp_host_value, str):
        raise ValueError("udp_host must be a string")
    if isinstance(udp_port_value, bool) or not isinstance(udp_port_value, int):
        raise ValueError("udp_port must be an integer")
    if isinstance(udp_listen_port_value, bool) or not isinstance(
        udp_listen_port_value, int
    ):
        raise ValueError("udp_listen_port must be an integer")
    if not isinstance(camera_url_value, str):
        raise ValueError("camera_url must be a string")

    udp_host = udp_host_value.strip()
    udp_port = udp_port_value
    udp_listen_port = udp_listen_port_value
    camera_url = camera_url_value.strip()

    udp_enabled = bool(udp_host or udp_port)
    if udp_enabled and (not udp_host or not 1 <= udp_port <= 65535):
        raise ValueError("udp_host and udp_port 1..65535 must both be set")
    if not 1 <= udp_listen_port <= 65535:
        raise ValueError("udp_listen_port must be in the range 1..65535")
    parsed_camera_url = urlparse(camera_url)
    if camera_url and (
        parsed_camera_url.scheme != "rtsp" or not parsed_camera_url.hostname
    ):
        raise ValueError("camera_url must be a valid RTSP URL")
    return udp_host, udp_port, udp_listen_port, camera_url, udp_enabled


async def send_client_message(websocket: WebSocket, message: dict[str, Any]) -> bool:
    """Serialize writes to one client and report whether it remains usable."""
    lock = runtime.clients.get(websocket)
    if lock is None:
        return False

    try:
        async with lock:
            await websocket.send_json(message)
        return True
    except (OSError, RuntimeError, WebSocketDisconnect):
        runtime.clients.pop(websocket, None)
        return False


async def broadcast_telemetry(packet: dict[str, Any]) -> None:
    if not runtime.clients:
        return
    await asyncio.gather(
        *(
            send_client_message(
                websocket,
                {"type": "receive", "packet": packet},
            )
            for websocket in tuple(runtime.clients)
        )
    )


async def broadcast_ping(ping_ms: float | None) -> None:
    if not runtime.clients:
        return

    await asyncio.gather(
        *(
            send_client_message(
                websocket,
                {"type": "ping", "ping_ms": ping_ms},
            )
            for websocket in tuple(runtime.clients)
        )
    )


async def udp_ping_loop() -> None:
    """Measure reachability of the configured UDP destination for the HUD."""
    while True:
        if runtime.clients:
            if not runtime.udp_enabled:
                await broadcast_ping(None)
                await asyncio.sleep(PING_INTERVAL_S)
                continue
            try:
                process = await asyncio.create_subprocess_exec(
                    "ping",
                    "-n",
                    "-c",
                    "1",
                    "-W",
                    str(int(PING_TIMEOUT_S)),
                    runtime.config.udp_ip,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                output, _ = await asyncio.wait_for(
                    process.communicate(), timeout=PING_TIMEOUT_S + 0.5
                )
                match = PING_VALUE_PATTERN.search(output.decode(errors="replace"))
                ping_ms = (
                    float(match.group(1)) if process.returncode == 0 and match else None
                )
            except (OSError, TimeoutError):
                ping_ms = None
            await broadcast_ping(ping_ms)
        await asyncio.sleep(PING_INTERVAL_S)


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
            if runtime.clients and runtime.udp_enabled:
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


async def udp_receive_loop() -> None:
    """Receive schema-defined robot telemetry on the configured local port."""
    loop = asyncio.get_running_loop()
    sock: socket.socket | None = None
    bound_port: int | None = None
    last_error_log = 0.0

    try:
        while True:
            configured_port = runtime.config.udp_listen_port
            if sock is None or configured_port != bound_port:
                if sock is not None:
                    sock.close()
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setblocking(False)
                try:
                    sock.bind(("0.0.0.0", configured_port))
                except OSError as error:
                    sock.close()
                    sock = None
                    bound_port = None
                    now = loop.time()
                    if now - last_error_log >= 1.0:
                        LOGGER.warning(
                            "UDP telemetry bind on port %s failed: %s",
                            configured_port,
                            error,
                        )
                        last_error_log = now
                    await asyncio.sleep(1.0)
                    continue
                bound_port = configured_port
                LOGGER.info("Listening for UDP telemetry on 0.0.0.0:%s", bound_port)

            try:
                payload, address = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, 65535), timeout=0.5
                )
            except TimeoutError:
                continue
            except OSError as error:
                now = loop.time()
                if now - last_error_log >= 1.0:
                    LOGGER.warning("UDP telemetry receive failed: %s", error)
                    last_error_log = now
                sock.close()
                sock = None
                bound_port = None
                continue

            try:
                packet = utils.decode_binary_packet(payload, PACKET_SCHEMA, "receive")
            except (TypeError, ValueError, KeyError) as error:
                now = loop.time()
                if now - last_error_log >= 1.0:
                    LOGGER.warning(
                        "Rejected UDP telemetry from %s:%s: %s",
                        address[0],
                        address[1],
                        error,
                    )
                    last_error_log = now
                continue

            await broadcast_telemetry(packet)
    finally:
        if sock is not None:
            sock.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    udp_tasks = (
        asyncio.create_task(udp_loop(), name="udp-sender"),
        asyncio.create_task(udp_receive_loop(), name="udp-receiver"),
        asyncio.create_task(udp_ping_loop(), name="udp-ping"),
    )
    try:
        yield
    finally:
        for task in udp_tasks:
            task.cancel()
        for task in udp_tasks:
            with suppress(asyncio.CancelledError):
                await task
        await video_stream.close()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Lightweight readiness probe for the container entrypoint."""
    return {"status": "ok"}


@app.post("/offer")
async def video_offer(request: Request) -> JSONResponse:
    try:
        params = await request.json()
        if not isinstance(params, dict):
            raise ValueError("WebRTC offer must be an object")
        sdp = params.get("sdp")
        offer_type = params.get("type")
        if not isinstance(sdp, str) or not isinstance(offer_type, str):
            raise ValueError("WebRTC offer requires string sdp and type fields")

        answer = await video_stream.create_answer(
            RTCSessionDescription(sdp=sdp, type=offer_type)
        )
        return JSONResponse(
            {"sdp": answer.sdp, "type": answer.type},
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    except Exception as error:
        LOGGER.warning("WebRTC offer failed: %s", error)
        return JSONResponse(
            {"error": "Unable to open the camera stream"}, status_code=503
        )


@app.websocket("/ws/controls")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    runtime.clients[websocket] = asyncio.Lock()
    LOGGER.info(
        "UI connected; sending controls every %.2f ms to %s:%s and receiving "
        "telemetry on port %s",
        utils.hz_to_ms(UDP_SEND_HZ),
        runtime.config.udp_ip,
        runtime.config.udp_port,
        runtime.config.udp_listen_port,
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
                    (
                        udp_host,
                        udp_port,
                        udp_listen_port,
                        camera_url,
                        udp_enabled,
                    ) = validate_config(config)
                    runtime.config.udp_ip = udp_host
                    runtime.config.udp_port = udp_port
                    runtime.config.udp_listen_port = udp_listen_port
                    runtime.config.camera_url = camera_url
                    runtime.udp_enabled = udp_enabled
                    await video_stream.update_url(camera_url)
                    print(
                        "UDP config accepted: "
                        f"enabled={udp_enabled} destination="
                        f"{udp_host}:{udp_port} telemetry_port={udp_listen_port} "
                        f"clients={len(runtime.clients)}",
                        flush=True,
                    )
                elif message_type == "send":
                    packet = incoming_data.get("packet", {})
                    if not isinstance(packet, dict):
                        raise ValueError("packet must be an object")
                    utils.validate_packet_values(packet, PACKET_SCHEMA)
                    next_packet = {**runtime.current_packet, **packet}
                    runtime.packet_payload = encode_packet(next_packet)
                    runtime.current_packet = next_packet
                else:
                    raise ValueError("message type must be 'config' or 'send'")
            except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
                if not await send_client_message(
                    websocket, {"type": "error", "message": str(error)}
                ):
                    break
    except WebSocketDisconnect:
        LOGGER.info("UI disconnected")
    finally:
        runtime.clients.pop(websocket, None)
        if not runtime.clients:
            runtime.current_packet = utils.generate_default_state(PACKET_SCHEMA)
            runtime.packet_payload = encode_packet(runtime.current_packet)
