import json
import socket
import struct
import time
import argparse
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "packets-schema.json"
WIRE_FORMATS = {
    "int8": ("b", 1),
    "uint8": ("B", 1),
    "int16": ("h", 2),
    "uint16": ("H", 2),
    "int32": ("i", 4),
    "uint32": ("I", 4),
    "float32": ("f", 4),
    "float64": ("d", 8),
}
UDP_IP = "0.0.0.0"
UDP_PORT = 8888
DEFAULT_TELEMETRY_HOST = "10.157.228.140"
DEFAULT_TELEMETRY_PORT = 8889
DEFAULT_TELEMETRY_INTERVAL = 1.0
# Encoder counts per unit of commanded drive, per second. The robot's real
# figure comes from its gearing; this only has to make the count move at a rate
# the HUD can be read against.
DEFAULT_ENCODER_COUNTS_PER_UNIT = 10.0
# How much of a steering command goes to the difference between the wheels.
# Enough to tell the two positions apart while turning, which is the point.
STEERING_SHARE = 0.5
LIST_PREVIEW = 6


class PacketLayout:
    """Schema-driven encode/decode that mirrors backend/utils.py.

    Repeated fields (``count``) are expanded element by element and the header
    is optional, so a packet type that declares ``"header": ""`` puts its
    fields on the wire on their own.
    """

    def __init__(self, packet_type: str, definition: dict[str, Any]) -> None:
        self.packet_type = packet_type
        self.byte_order = {"little": "<", "big": ">"}[definition["byte_order"]]
        self.header = (definition.get("header") or "").encode("ascii")
        self.fields = definition["fields"]
        self.elements: list[tuple[str, int | None, str, int]] = []
        for field in self.fields:
            count = int(field.get("count", 1) or 1)
            code, size = WIRE_FORMATS[field["type"]]
            for index in range(count):
                self.elements.append(
                    (field["name"], index if count > 1 else None, code, size)
                )
        self.struct = struct.Struct(
            self.byte_order + "".join(element[2] for element in self.elements)
        )
        self.size = len(self.header) + self.struct.size

    def decode(self, payload: bytes) -> tuple[dict[str, Any], list[str]]:
        """Decode as much as the datagram holds; never reject it outright."""
        notes: list[str] = []
        body = payload
        if self.header:
            if payload.startswith(self.header):
                body = payload[len(self.header) :]
            else:
                notes.append(
                    f"header mismatch: expected {self.header!r}, "
                    f"got {payload[: len(self.header)]!r} (decoding from offset 0)"
                )
        if len(payload) != self.size:
            notes.append(f"length {len(payload)}, schema expects {self.size}")

        decoded: dict[str, Any] = {}
        offset = 0
        truncated = False
        for name, index, code, size in self.elements:
            if offset + size > len(body):
                notes.append(f"truncated at field {name!r}")
                truncated = True
                break
            (value,) = struct.unpack_from(self.byte_order + code, body, offset)
            offset += size
            if index is None:
                decoded[name] = value
            else:
                decoded.setdefault(name, []).append(value)

        extra = len(body) - offset
        if extra > 0 and not truncated:
            notes.append(f"{extra} trailing byte(s) ignored")
        return decoded, notes

    def encode(self, overrides: dict[str, Any]) -> bytes:
        values: list[Any] = []
        for field in self.fields:
            name = field["name"]
            count = int(field.get("count", 1) or 1)
            default = field.get(
                "default", 0.0 if field["type"].startswith("float") else 0
            )
            value = clamp(field, overrides.get(name, default))
            values.extend([value] * count)
        return self.header + self.struct.pack(*values)


def clamp(field: dict[str, Any], value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "min" in field and value < field["min"]:
            return field["min"]
        if "max" in field and value > field["max"]:
            return field["max"]
    return value


def field_by_role(layout: PacketLayout, role: str) -> dict[str, Any] | None:
    """Find a field by the job it does rather than by its name."""
    for field in layout.fields:
        if field.get("role") == role:
            return field
    return None


def load_layout(packet_type: str) -> PacketLayout:
    with SCHEMA_PATH.open(encoding="utf-8") as schema_file:
        schema = json.load(schema_file)
    return PacketLayout(packet_type, schema["packet_types"][packet_type])


def summarize(decoded: dict[str, Any]) -> str:
    """Keep long padding arrays from burying the interesting fields."""
    parts = []
    for name, value in decoded.items():
        if isinstance(value, list) and len(value) > LIST_PREVIEW:
            head = ", ".join(str(item) for item in value[:LIST_PREVIEW])
            parts.append(f"{name}=[{head}, ... +{len(value) - LIST_PREVIEW} more]")
        else:
            parts.append(f"{name}={value}")
    return ", ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Receive robot commands and send dummy wheel telemetry"
    )
    parser.add_argument("--listen-host", default=UDP_IP)
    parser.add_argument("--listen-port", type=int, default=UDP_PORT)
    parser.add_argument("--telemetry-host", default=DEFAULT_TELEMETRY_HOST)
    parser.add_argument("--telemetry-port", type=int, default=DEFAULT_TELEMETRY_PORT)
    parser.add_argument(
        "--encoder-counts-per-unit",
        type=float,
        default=DEFAULT_ENCODER_COUNTS_PER_UNIT,
        help=(
            "encoder counts added per unit of commanded drive per second; "
            "0 leaves the count still whatever is commanded"
        ),
    )
    parser.add_argument(
        "--telemetry-interval",
        type=float,
        default=DEFAULT_TELEMETRY_INTERVAL,
        help="seconds between dummy receive packets",
    )
    parser.add_argument(
        "--hex",
        action="store_true",
        help="print the raw bytes of every datagram received",
    )
    args = parser.parse_args()

    send_layout = load_layout("send")

    # Telemetry is a convenience, not the point of the simulator: if the
    # receive schema cannot be encoded we still keep listening.
    telemetry_layout: PacketLayout | None = None
    try:
        telemetry_layout = load_layout("receive")
    except (KeyError, ValueError, struct.error) as error:
        print(f"Telemetry disabled, receive schema unusable: {error}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    telemetry_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.1)
    sock.bind((args.listen_host, args.listen_port))
    print(
        f"Listening for UDP commands on {args.listen_host}:{args.listen_port} "
        f"(schema expects {send_layout.size} bytes, header "
        f"{send_layout.header!r}); telemetry to "
        f"{args.telemetry_host}:{args.telemetry_port}"
    )

    # The wheels are driven by what is being commanded rather than by the
    # clock: a robot that is not being driven does not travel, and a distance
    # readout that climbs while the sticks are centred would test nothing.
    # Steering splits the two sides apart, so a turn is visible as the left and
    # right positions drifting from each other.
    drive_field = field_by_role(send_layout, "yVelocity")
    steering_field = field_by_role(send_layout, "thetaVelocity")

    last_arrival: float | None = None
    next_telemetry = time.monotonic()
    position_left = 0.0
    position_right = 0.0
    commanded_drive = 0.0
    commanded_steering = 0.0
    wheels_advanced_at = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            if telemetry_layout is not None and now >= next_telemetry:
                elapsed = now - wheels_advanced_at
                wheels_advanced_at = now
                # Positions are signed: reversing winds them back, which is
                # what the robot's own encoders do.
                speed_left = (
                    commanded_drive - commanded_steering * STEERING_SHARE
                ) * args.encoder_counts_per_unit
                speed_right = (
                    commanded_drive + commanded_steering * STEERING_SHARE
                ) * args.encoder_counts_per_unit
                position_left += speed_left * elapsed
                position_right += speed_right * elapsed
                try:
                    telemetry_sock.sendto(
                        telemetry_layout.encode(
                            {
                                "position_left": position_left,
                                "position_right": position_right,
                                "speed_left": speed_left,
                                "speed_right": speed_right,
                            }
                        ),
                        (args.telemetry_host, args.telemetry_port),
                    )
                    print(
                        f"Sent receive packet: position left={position_left:.1f} "
                        f"right={position_right:.1f}, speed left={speed_left:.1f} "
                        f"right={speed_right:.1f}"
                    )
                except (OSError, struct.error) as error:
                    print(f"Telemetry send failed: {error}")
                next_telemetry = now + args.telemetry_interval

            try:
                data, address = sock.recvfrom(65535)
            except TimeoutError:
                continue

            arrived_at = time.perf_counter()
            interval_ms = (
                None if last_arrival is None else (arrived_at - last_arrival) * 1000.0
            )
            last_arrival = arrived_at
            timing = "first packet" if interval_ms is None else f"{interval_ms:.2f} ms"

            decoded, notes = send_layout.decode(data)
            if drive_field is not None:
                value = decoded.get(drive_field["name"])
                commanded_drive = value if isinstance(value, (int, float)) else 0.0
            if steering_field is not None:
                value = decoded.get(steering_field["name"])
                commanded_steering = value if isinstance(value, (int, float)) else 0.0
            print(f"{address} | {timing} | {len(data)} B | {summarize(decoded)}")
            for note in notes:
                print(f"  ! {note}")
            if args.hex:
                print(f"  raw: {data.hex(' ')}")
    finally:
        sock.close()
        telemetry_sock.close()


if __name__ == "__main__":
    main()
