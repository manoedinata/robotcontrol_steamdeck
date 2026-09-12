import asyncio
import json
import logging
import math
import os
import re
import socket
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aiortc import RTCSessionDescription
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.cors import CORSMiddleware
import settings as settings_module
from PTZController import (
    PTZ_PASSWORD,
    PTZ_USERNAME,
    PTZController,
    normalize_direction,
    normalize_focus,
    normalize_zoom,
)
from CameraWebSocketSource import CameraWebSocketHub
from Recorder import Recorder, normalize_record_action
from WebRTCStream import WebRTCStream
import utils

LOGGER = logging.getLogger(__name__)
UDP_SEND_HZ = 50
PTZ_SEND_HZ = 5.0
PING_INTERVAL_S = 2.0
RECORDING_STATE_INTERVAL_S = 2.0
PING_TIMEOUT_S = 1.0
PING_VALUE_PATTERN = re.compile(r"time[=<]([0-9]+(?:\.[0-9]+)?)\s*ms")

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "packets-schema.json"
with SCHEMA_PATH.open(encoding="utf-8") as schema_file:
    PACKET_SCHEMA = json.load(schema_file)


SCHEMA_SLEW_RATES = utils.slew_rates(PACKET_SCHEMA)
# Ramp state is kept as float so a sub-unit step still makes progress, but an
# integer field cannot carry one, so these are rounded on the way out.
INTEGER_SEND_FIELDS = frozenset(
    field["name"]
    for field in utils.packet_schema(PACKET_SCHEMA, "send")["fields"]
    if not field["type"].startswith("float")
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)


@dataclass
class RuntimeState:
    config: settings_module.Settings
    # What the UI last asked for, and what the ramp has actually reached. They
    # differ only while a rate-limited field is still travelling toward its
    # target; every other field tracks the target exactly.
    target_packet: dict[str, Any]
    current_packet: dict[str, Any]
    packet_payload: bytes
    udp_enabled: bool = False
    clients: dict[WebSocket, asyncio.Lock] = field(default_factory=dict)
    udp_socket: socket.socket | None = None
    ptz: PTZController | None = None
    # Latest rotation and zoom requests from any UI. None means "no button
    # held", which the ptz loop turns into a continuous stop command
    # (deadman behavior).
    ptz_request: str | None = None
    ptz_zoom_request: str | None = None
    ptz_focus_request: str | None = None
    # Tracks which focus command was last sent to the camera so the loop can
    # fire on the press/release edges only instead of re-sending each tick.
    ptz_focus_sent: str | None = None
    ptz_request_seq: int = 0


def encode_packet(packet: dict[str, Any]) -> bytes:
    return utils.encode_binary_packet(packet, PACKET_SCHEMA)


def encode_current_packet() -> bytes:
    """Encode the ramped packet, rounding the fields that must be integers."""
    return encode_packet(
        {
            name: (
                round(value)
                if name in INTEGER_SEND_FIELDS and isinstance(value, float)
                else value
            )
            for name, value in runtime.current_packet.items()
        }
    )


default_packet = utils.generate_default_state(PACKET_SCHEMA)
runtime = RuntimeState(
    config=settings_module.Settings(
        # udp_ip="192.168.1.153",
        udp_port=8888,
        udp_listen_port=8889,
        camera_streams=(),
        camera_backend="go2rtc",
    ),
    target_packet=dict(default_packet),
    current_packet=dict(default_packet),
    packet_payload=encode_packet(default_packet),
)


# Direct camera WebSocket sources are pulled in here and re-served over HTTP,
# so the camera backend below can consume them exactly like an RTSP source.
camera_ws_hub = CameraWebSocketHub(logger=LOGGER)

video_stream = WebRTCStream(
    runtime.config.camera_streams,
    logger=LOGGER,
    backend=runtime.config.camera_backend,
    ws_hub=camera_ws_hub,
)

# Writes every configured source to disk on request. Constructing it does no
# work; nothing is opened until the operator starts a recording.
recorder = Recorder(logger=LOGGER, ws_hub=camera_ws_hub)

# A camera stream id is echoed straight into a go2rtc URL and used as a
# config-map key, so keep it to an unambiguous, injection-safe alphabet.
CAMERA_STREAM_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

# RTSP is dialed by the camera backend; ws/wss is pulled in by the backend and
# re-served over HTTP so the same backend can turn it into WebRTC.
CAMERA_STREAM_SCHEMES = frozenset({"rtsp", "ws", "wss"})


def validate_camera_streams(value: Any) -> tuple[tuple[str, str], ...]:
    """Validate the list of camera sources kept warm for instant switching.

    Each entry is ``{"id": <stream id>, "url": <camera url>}``. Ids must be
    unique and match ``CAMERA_STREAM_ID_RE``. A url is either RTSP, dialed by
    the camera backend directly, or a direct camera WebSocket, which the
    backend re-serves over HTTP so the same backend can consume it. Both kinds
    reach the renderer as WebRTC through ``POST /offer?src=<id>``. An empty
    list means no camera source and keeps every transport idle.
    """
    if not isinstance(value, list):
        raise ValueError("camera_streams must be a list")

    streams: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError("each camera stream must be an object")
        stream_id = entry.get("id")
        url_value = entry.get("url")
        if not isinstance(stream_id, str) or not CAMERA_STREAM_ID_RE.fullmatch(
            stream_id
        ):
            raise ValueError("camera stream id must match [A-Za-z0-9_-]{1,64}")
        if stream_id in seen_ids:
            raise ValueError(f"duplicate camera stream id: {stream_id!r}")
        if not isinstance(url_value, str):
            raise ValueError("camera stream url must be a string")
        url = url_value.strip()
        parsed = urlparse(url)
        if (
            not url
            or parsed.scheme not in CAMERA_STREAM_SCHEMES
            or not parsed.hostname
        ):
            raise ValueError(
                "camera stream url must be a valid RTSP or WebSocket URL"
            )
        seen_ids.add(stream_id)
        streams.append((stream_id, url))
    return tuple(streams)


def validate_recordings_dir(value: Any) -> str:
    """Validate the operator's recordings location.

    Empty means "use the deployment default" (the RECORDINGS_DIR environment
    variable, or a directory beside the repo), which is what the Docker image
    and the Steam launcher set up. An absolute path is required: a relative one
    would resolve against whatever directory the backend happens to be started
    from, which is not something the operator can reason about.

    The path is only ever used as the parent of a generated session directory,
    and every component below it is sanitized, so an operator naming an awkward
    directory can misplace their own recordings but cannot make the backend
    write outside the directory they chose.
    """
    if not isinstance(value, str):
        raise ValueError("recordings_dir must be a string")
    path = value.strip()
    if not path:
        return ""
    if not os.path.isabs(path):
        raise ValueError("recordings_dir must be an absolute path")
    return path


def validate_packet_slew(value: Any) -> dict[str, float]:
    """Validate the operator's per-field ramp rates, in units per second.

    Keys name send-packet fields that carry a command; padding is not ramped,
    so naming it is a mistake worth reporting rather than ignoring. A rate of
    0 disables limiting for that field, and a field left out keeps the rate
    the schema declares.
    """
    if not isinstance(value, dict):
        raise ValueError("packet_slew must be an object")

    commandable = {
        field["name"]
        for field in utils.packet_schema(PACKET_SCHEMA, "send")["fields"]
        if field.get("role") != "padding"
    }
    rates: dict[str, float] = {}
    for name, rate in value.items():
        if name not in commandable:
            raise ValueError(f"Unknown packet_slew field: {name!r}")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            raise ValueError(f"packet_slew {name!r} must be a number")
        if not math.isfinite(rate) or rate < 0:
            raise ValueError(f"packet_slew {name!r} must be finite and not negative")
        rates[name] = float(rate)
    return rates


def validate_config(
    config: dict[str, Any],
) -> tuple[
    str,
    int,
    int,
    tuple[tuple[str, str], ...],
    str,
    bool,
    str,
    dict[str, float],
    str,
]:
    allowed_keys = {
        "udp_host",
        "udp_port",
        "udp_listen_port",
        "camera_streams",
        "camera_backend",
        "ptz_ip",
        "packet_slew",
        "recordings_dir",
    }
    unknown_keys = set(config) - allowed_keys
    if unknown_keys:
        raise ValueError(f"Unknown config fields: {sorted(unknown_keys)}")

    udp_host_value = config.get("udp_host", runtime.config.udp_ip)
    udp_port_value = config.get("udp_port", runtime.config.udp_port)
    udp_listen_port_value = config.get(
        "udp_listen_port", runtime.config.udp_listen_port
    )
    camera_streams = (
        validate_camera_streams(config["camera_streams"])
        if "camera_streams" in config
        else runtime.config.camera_streams
    )
    camera_backend_value = config.get("camera_backend", runtime.config.camera_backend)
    ptz_ip_value = config.get("ptz_ip", runtime.config.ptz_ip)
    packet_slew = (
        validate_packet_slew(config["packet_slew"])
        if "packet_slew" in config
        else dict(runtime.config.packet_slew)
    )
    recordings_dir = (
        validate_recordings_dir(config["recordings_dir"])
        if "recordings_dir" in config
        else runtime.config.recordings_dir
    )
    if not isinstance(udp_host_value, str):
        raise ValueError("udp_host must be a string")
    if isinstance(udp_port_value, bool) or not isinstance(udp_port_value, int):
        raise ValueError("udp_port must be an integer")
    if isinstance(udp_listen_port_value, bool) or not isinstance(
        udp_listen_port_value, int
    ):
        raise ValueError("udp_listen_port must be an integer")
    if not isinstance(camera_backend_value, str) or camera_backend_value not in {
        "go2rtc",
        "aiortc",
    }:
        raise ValueError("camera_backend must be 'go2rtc' or 'aiortc'")
    if not isinstance(ptz_ip_value, str):
        raise ValueError("ptz_ip must be a string")

    udp_host = udp_host_value.strip()
    udp_port = udp_port_value
    udp_listen_port = udp_listen_port_value
    ptz_ip = ptz_ip_value.strip()

    udp_enabled = bool(udp_host or udp_port)
    if udp_enabled and (not udp_host or not 1 <= udp_port <= 65535):
        raise ValueError("udp_host and udp_port 1..65535 must both be set")
    if not 1 <= udp_listen_port <= 65535:
        raise ValueError("udp_listen_port must be in the range 1..65535")
    return (
        udp_host,
        udp_port,
        udp_listen_port,
        camera_streams,
        camera_backend_value,
        udp_enabled,
        ptz_ip,
        packet_slew,
        # Appended last on purpose: the existing tests index this tuple
        # positionally, so a new field must never shift the ones before it.
        recordings_dir,
    )


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

    LOGGER.warning("PING UDP COKKK: " + str(ping_ms))

    await asyncio.gather(
        *(
            send_client_message(
                websocket,
                {"type": "ping", "ping_ms": ping_ms},
            )
            for websocket in tuple(runtime.clients)
        )
    )


async def broadcast_recording(state: dict[str, Any]) -> None:
    if not runtime.clients:
        return

    await asyncio.gather(
        *(
            send_client_message(websocket, state)
            for websocket in tuple(runtime.clients)
        )
    )


async def recording_state_loop() -> None:
    """Refresh recording counters and publish them while a session runs.

    Also drives the stall watchdog and the free-space check, so one tick does
    all three rather than each keeping its own timer.
    """
    while True:
        try:
            if recorder.active:
                state = await recorder.poll()
                await broadcast_recording(state)
        except asyncio.CancelledError:
            raise
        except Exception:
            # This tick also drives the stall watchdog and the free-space
            # guard, so letting it die would silently disarm both while a
            # recording kept running.
            LOGGER.exception("Recording state tick failed")
        await asyncio.sleep(RECORDING_STATE_INTERVAL_S)


async def udp_ping_loop() -> None:
    """Measure reachability of the configured UDP destination for the HUD."""
    while True:
        if runtime.clients:
            if not runtime.udp_enabled:
                # print("PING: UDP BLM NYALA COKK")
                await broadcast_ping(None)
                await asyncio.sleep(PING_INTERVAL_S)
                continue
            try:
                # print("PING: LAGI NGIRIMMMM")
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
            except (OSError, TimeoutError) as e:
                LOGGER.warning("PING: GAGAL COKK")
                LOGGER.warning("PING: " + str(OSError))
                LOGGER.warning("PING: " + str(TimeoutError))
                LOGGER.warning("PING: " + str(e))
                ping_ms = None
            await broadcast_ping(ping_ms)
        await asyncio.sleep(PING_INTERVAL_S)


def effective_slew_rates() -> dict[str, float]:
    """Ramp rates in force: the operator's overrides over the schema's."""
    return {**SCHEMA_SLEW_RATES, **runtime.config.packet_slew}


def advance_packet(dt: float) -> bool:
    """Step the outgoing packet toward the UI's target. True if it moved.

    Fields with a rate travel at most `rate * dt` per tick; the rest take the
    target immediately. State is kept as float even for integer fields --
    rounding it here would strand any field whose per-tick step is under half
    a unit, so rounding is left to the encoder.
    """
    rates = effective_slew_rates()
    moved = False
    for name, target in runtime.target_packet.items():
        current = runtime.current_packet.get(name)
        if isinstance(target, list) or not isinstance(current, (int, float)):
            # Padding and any other non-scalar field is not ramped.
            if current != target:
                runtime.current_packet[name] = target
                moved = True
            continue
        stepped = utils.slew_step(float(current), float(target), rates.get(name), dt)
        if stepped != current:
            runtime.current_packet[name] = stepped
            moved = True
    return moved


def release_udp_socket(sock: socket.socket | None) -> None:
    """Close a telemetry socket and stop the send loop from reaching for it.

    The send and receive loops share one bound socket so the robot sees
    commands arrive from the same port its telemetry is sent to. That makes
    the receiver the socket's owner, and every close has to clear the shared
    reference: otherwise the send loop keeps calling sendto() on a closed
    descriptor and fails with EBADF on every tick.
    """
    if sock is None:
        return
    if runtime.udp_socket is sock:
        runtime.udp_socket = None
    sock.close()


async def udp_loop() -> None:
    """Send the latest controls at a stable rate while a UI is connected."""
    loop = asyncio.get_running_loop()
    interval = utils.hz_to_s(UDP_SEND_HZ)
    # A tick that ran late must not hand the ramp one huge step, so the
    # measured dt is capped at a few intervals' worth of catch-up.
    max_dt = interval * 5
    next_send = loop.time()
    last_tick = next_send
    last_error_log = 0.0

    try:
        while True:
            now = loop.time()
            dt = min(max(now - last_tick, 0.0), max_dt)
            last_tick = now
            if advance_packet(dt):
                runtime.packet_payload = encode_current_packet()

            if runtime.clients and runtime.udp_enabled:
                # Read the shared socket once: the receive loop may rebind it
                # between the check and the send.
                sock = runtime.udp_socket
                if sock is None:
                    # Nothing is bound, so there is nothing to send from. Say
                    # so instead of going quiet, which looks identical to a
                    # robot that is simply not answering.
                    now = loop.time()
                    if now - last_error_log >= 1.0:
                        LOGGER.warning(
                            "UDP send skipped: telemetry port %s is not bound",
                            runtime.config.udp_listen_port,
                        )
                        last_error_log = now
                else:
                    try:
                        # Send using the shared, bound socket
                        sock.sendto(
                            runtime.packet_payload,
                            (runtime.config.udp_ip, runtime.config.udp_port),
                        )
                    except BlockingIOError:
                        # Non-blocking socket OS buffer is full, skip this tick
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


def send_field_limits() -> list[dict[str, Any]]:
    """Bounds of every operator-settable field of the send packet.

    The UI renders one min/max row per entry and clamps its joystick output to
    the operator's values, which must stay inside the schema bounds returned
    here. Padding carries no operator-visible value, so it is left out.
    """
    return [
        {
            "name": field["name"],
            "role": field.get("role", field["name"]),
            "type": field["type"],
            "min": field.get("min"),
            "max": field.get("max"),
            "default": field.get("default", utils.default_for_wire_type(field["type"])),
            # Units per second the command may change by. None leaves the
            # field unramped.
            "slew_rate": effective_slew_rates().get(field["name"]),
        }
        for field in utils.packet_schema(PACKET_SCHEMA, "send")["fields"]
        if field.get("role") != "padding"
    ]


def sync_ptz_controller() -> None:
    """Rebuild the PTZ controller when its settings change."""
    ptz_ip = runtime.config.ptz_ip
    if not ptz_ip:
        if runtime.ptz is not None:
            runtime.ptz = None
            LOGGER.info("PTZ control disabled: no ptz_ip configured")
        return

    if runtime.ptz is None or runtime.ptz.ip != ptz_ip:
        runtime.ptz = PTZController(
            ip=ptz_ip,
            username=PTZ_USERNAME,
            password=PTZ_PASSWORD,
        )
        LOGGER.info("PTZ control enabled for %s", ptz_ip)


async def ptz_loop() -> None:
    """Deadman loop for camera rotation and zoom, edge-triggered focus.

    Rotation and zoom are re-sent (left/right/up/down rotation, zoom-in/
    zoom-out) at a fixed rate while a button is held, and continuously send
    the ISAPI stop command while nothing is pressed. This keeps the camera
    from moving forever if a stop request from the UI is lost.

    Focus is edge-triggered: exactly one FocusData command is sent when a
    focus button is pressed and one FocusData stop when it is released, so
    the focus endpoint is not hammered at the loop rate.
    """
    loop = asyncio.get_running_loop()
    interval = utils.hz_to_s(PTZ_SEND_HZ)
    next_send = loop.time()
    last_error_log = 0.0

    try:
        while True:
            direction = runtime.ptz_request
            zoom = runtime.ptz_zoom_request
            focus = runtime.ptz_focus_request
            controller = runtime.ptz
            if controller is not None:
                try:
                    if direction is not None:
                        await controller.move(direction)
                    elif zoom is not None:
                        await controller.zoom(zoom)
                    else:
                        # Nothing requested: re-send the stop command every
                        # tick, so a lost stop can never leave the camera
                        # moving on its own.
                        await controller.stop()
                    # Focus runs on its own ISAPI endpoint (FocusData), but it
                    # is edge-triggered instead of a deadman: one command is
                    # sent when the button is pressed and one stop when it is
                    # released, instead of re-sending every tick.
                    if focus is not None and focus != runtime.ptz_focus_sent:
                        await controller.focus(focus)
                        runtime.ptz_focus_sent = focus
                    elif focus is None and runtime.ptz_focus_sent is not None:
                        await controller.stop_focus()
                        runtime.ptz_focus_sent = None
                except Exception as error:
                    now = loop.time()
                    if now - last_error_log >= 1.0:
                        active = (
                            f"move {direction or zoom or focus}"
                            if (direction or zoom or focus)
                            else "stop"
                        )
                        LOGGER.warning(
                            "PTZ %s to %s failed: %s",
                            active,
                            controller.ip,
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
        raise
    except Exception as error:
        LOGGER.error("PTZ loop crashed unexpectedly: %s", error)


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
                release_udp_socket(sock)
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setblocking(False)
                try:
                    sock.bind(("0.0.0.0", configured_port))
                    runtime.udp_socket = sock  # <-- Share the socket here
                except OSError as error:
                    release_udp_socket(sock)
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
                release_udp_socket(sock)
                sock = None
                bound_port = None
                continue

            # LOGGER.warning(
            #     "Received UDP telemetry from %s:%s, payload=%s",
            #     address[0],
            #     address[1],
            #     payload,
            # )

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
        release_udp_socket(sock)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    recorder.set_listener(broadcast_recording)
    udp_tasks = (
        asyncio.create_task(udp_loop(), name="udp-sender"),
        asyncio.create_task(udp_receive_loop(), name="udp-receiver"),
        asyncio.create_task(udp_ping_loop(), name="udp-ping"),
        asyncio.create_task(ptz_loop(), name="ptz-deadman"),
        asyncio.create_task(recording_state_loop(), name="recording-state"),
    )
    try:
        yield
    finally:
        for task in udp_tasks:
            task.cancel()
        for task in udp_tasks:
            with suppress(asyncio.CancelledError):
                await task
        # Recorders first so their files are trailered and their camera
        # sessions released, then the peers, whose relay subscriptions are what
        # keep the hub's camera connections open.
        recorder.set_listener(None)
        await recorder.close()
        await video_stream.close()
        await camera_ws_hub.close()


app = FastAPI(lifespan=lifespan)

# The renderer is served by Vite on a different origin during development
# (http://127.0.0.1:5173), so signaling via POST /offer needs CORS headers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Lightweight readiness probe for the container entrypoint."""
    return {"status": "ok"}


@app.get("/storage/targets")
async def storage_targets() -> JSONResponse:
    """Recording destinations the Settings picker offers.

    The operator chooses a card, not a path: mount points are named after a
    card's label or its UUID, so a path is neither guessable nor worth showing.
    """
    return JSONResponse(recorder.storage_targets())


@app.get("/camera/{stream_id}/stream")
async def camera_relay(stream_id: str, preroll: bool = False) -> StreamingResponse:
    """Re-serve one direct camera WebSocket source as an HTTP byte stream.

    This exists because neither go2rtc nor ffmpeg can read a WebSocket. It is
    an internal seam between the hub and the camera backend, not a renderer
    endpoint: the UI still gets every source as WebRTC from ``POST /offer``.

    ``?preroll=1`` starts the stream at the camera's last keyframe instead of
    at the next one, which is what lets a recording begin when the operator
    pressed record. Only the recorder asks for it; the live path would open on
    stale video.
    """

    # Checked before the response starts, so an unknown id is a 404 rather than
    # an empty 200 that ffmpeg would happily retry against forever.
    if not camera_ws_hub.has_stream(stream_id):
        return JSONResponse(
            {"error": f"unknown camera stream: {stream_id!r}"}, status_code=404
        )

    async def payloads():
        async with camera_ws_hub.subscribe(stream_id, preroll=preroll) as stream:
            async for payload in stream:
                yield payload

    return StreamingResponse(payloads(), media_type="application/octet-stream")


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

        # The renderer names the camera source through ?src=<stream id>; it is
        # optional for a single-stream setup.
        stream_id = request.query_params.get("src") or None

        answer = await video_stream.create_answer(
            RTCSessionDescription(sdp=sdp, type=offer_type), stream_id
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
    # The send-packet layout is the backend's to own, so the UI is told which
    # fields it may bound instead of parsing the schema itself.
    await send_client_message(
        websocket, {"type": "schema", "fields": send_field_limits()}
    )
    # A recording outlives a UI reconnect, so the backend states the truth
    # rather than letting the renderer replay a stale intent.
    await send_client_message(websocket, recorder.state())
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
                    # print("JANCOKKKK DAPET DATA DARI WEBUI")
                    config = incoming_data.get("config", {})
                    if not isinstance(config, dict):
                        raise ValueError("config must be an object")
                    (
                        udp_host,
                        udp_port,
                        udp_listen_port,
                        camera_streams,
                        camera_backend,
                        udp_enabled,
                        ptz_ip,
                        packet_slew,
                        recordings_dir,
                    ) = validate_config(config)
                    runtime.config.udp_ip = udp_host
                    runtime.config.udp_port = udp_port
                    runtime.config.udp_listen_port = udp_listen_port
                    runtime.config.camera_streams = camera_streams
                    runtime.config.camera_backend = camera_backend
                    runtime.udp_enabled = udp_enabled
                    runtime.config.ptz_ip = ptz_ip
                    runtime.config.packet_slew = packet_slew
                    runtime.config.recordings_dir = recordings_dir
                    sync_ptz_controller()
                    # The hub must know a WebSocket source before the camera
                    # backend is pointed at its relay URL, or the first dial
                    # 404s.
                    await camera_ws_hub.update_streams(camera_streams)
                    await video_stream.update_config(camera_streams, camera_backend)
                    recorder.update_sources(camera_streams)
                    recorder.set_root(recordings_dir)
                    LOGGER.info(
                        "UDP config accepted: "
                        f"enabled={udp_enabled} destination="
                        f"{udp_host}:{udp_port} telemetry_port={udp_listen_port} "
                        f"ptz_ip={ptz_ip or 'disabled'} "
                        f"camera_streams={len(camera_streams)} "
                        f"clients={len(runtime.clients)}"
                    )
                elif message_type == "ptz":
                    # UI PTZ request: direction is "left", "right", "up", or
                    # "down" while a D-Pad button is held; zoom is
                    # "zoom-in"/"zoom-out" while RB/LB is held; focus is
                    # "focus-near"/"focus-far" while a focus button is held.
                    # Any may be null when nothing is held (triggers stop).
                    direction = incoming_data.get("direction")
                    zoom = incoming_data.get("zoom")
                    focus = incoming_data.get("focus")
                    if direction is not None:
                        direction = normalize_direction(direction)
                    if zoom is not None:
                        zoom = normalize_zoom(zoom)
                    if focus is not None:
                        focus = normalize_focus(focus)
                    runtime.ptz_request = direction
                    runtime.ptz_zoom_request = zoom
                    runtime.ptz_focus_request = focus
                    runtime.ptz_request_seq += 1
                elif message_type == "record":
                    action = normalize_record_action(incoming_data.get("action"))
                    if action == "start":
                        await recorder.start()
                    else:
                        await recorder.stop()
                elif message_type == "send":
                    packet = incoming_data.get("packet", {})
                    if not isinstance(packet, dict):
                        raise ValueError("packet must be an object")
                    utils.validate_packet_values(packet, PACKET_SCHEMA)
                    # Only the target moves here. udp_loop ramps the packet on
                    # the wire toward it at the configured rate.
                    runtime.target_packet = {**runtime.target_packet, **packet}
                else:
                    raise ValueError(
                        "message type must be 'config', 'send', 'ptz', or 'record'"
                    )
            except (
                TypeError,
                ValueError,
                KeyError,
                RuntimeError,
                json.JSONDecodeError,
            ) as error:
                if not await send_client_message(
                    websocket, {"type": "error", "message": str(error)}
                ):
                    break
    except WebSocketDisconnect:
        LOGGER.info("UI disconnected")
    finally:
        runtime.clients.pop(websocket, None)
        if not runtime.clients:
            # Clear the ramp along with the target: a UI that reconnects must
            # start from a standstill, not resume a half-finished ramp.
            runtime.target_packet = utils.generate_default_state(PACKET_SCHEMA)
            runtime.current_packet = dict(runtime.target_packet)
            runtime.packet_payload = encode_current_packet()
            # Last UI left; make sure the camera stops rotating, zooming, and
            # focusing. ptz_focus_sent is intentionally NOT reset here: the
            # loop sees focus=None with a non-None sent-state as the release
            # edge and sends exactly one FocusData stop itself.
            runtime.ptz_request = None
            runtime.ptz_zoom_request = None
            runtime.ptz_focus_request = None
