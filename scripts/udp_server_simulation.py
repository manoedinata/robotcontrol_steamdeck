import json
import random
import socket
import struct
import time
import argparse
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "packets-schema.json"
WIRE_FORMATS = {
    "int8": "b",
    "uint8": "B",
    "int16": "h",
    "uint16": "H",
    "int32": "i",
    "uint32": "I",
    "float32": "f",
    "float64": "d",
}
UDP_IP = "0.0.0.0"
UDP_PORT = 8888
DEFAULT_TELEMETRY_HOST = "192.168.1.151"
DEFAULT_TELEMETRY_PORT = 8889
DEFAULT_BATTERY_LEVEL = 75
DEFAULT_TELEMETRY_INTERVAL = 1.0


def load_packet_layout() -> tuple[bytes, struct.Struct, list[str]]:
    with SCHEMA_PATH.open(encoding="utf-8") as schema_file:
        command = json.load(schema_file)["packet_types"]["send"]

    byte_order = {"little": "<", "big": ">"}[command["byte_order"]]
    fields = command["fields"]
    packet_struct = struct.Struct(
        byte_order + "".join(WIRE_FORMATS[field["type"]] for field in fields)
    )
    return (
        command["header"].encode("ascii"),
        packet_struct,
        [field["name"] for field in fields],
    )


def encode_receive_packet(battery_level: int) -> bytes:
    with SCHEMA_PATH.open(encoding="utf-8") as schema_file:
        receive = json.load(schema_file)["packet_types"]["receive"]

    fields = receive["fields"]
    if [field["name"] for field in fields] != ["battery_level"]:
        raise ValueError("Simulator requires one receive field named battery_level")
    field = fields[0]
    if not field.get("min", 0) <= battery_level <= field.get("max", 100):
        raise ValueError("battery level is outside schema bounds")

    byte_order = {"little": "<", "big": ">"}[receive["byte_order"]]
    packet_struct = struct.Struct(byte_order + WIRE_FORMATS[field["type"]])
    return receive["header"].encode("ascii") + packet_struct.pack(battery_level)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Receive robot commands and send dummy battery telemetry"
    )
    parser.add_argument("--listen-host", default=UDP_IP)
    parser.add_argument("--listen-port", type=int, default=UDP_PORT)
    parser.add_argument("--telemetry-host", default=DEFAULT_TELEMETRY_HOST)
    parser.add_argument("--telemetry-port", type=int, default=DEFAULT_TELEMETRY_PORT)
    parser.add_argument("--battery-level", type=int, default=DEFAULT_BATTERY_LEVEL)
    parser.add_argument(
        "--telemetry-interval",
        type=float,
        default=DEFAULT_TELEMETRY_INTERVAL,
        help="seconds between dummy receive packets",
    )
    args = parser.parse_args()

    header, packet_struct, field_names = load_packet_layout()
    expected_size = len(header) + packet_struct.size
    telemetry_payload = encode_receive_packet(args.battery_level)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    telemetry_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.1)
    sock.bind((args.listen_host, args.listen_port))
    print(
        f"Listening for {expected_size}-byte UDP commands on "
        f"{args.listen_host}:{args.listen_port} and sending "
        f"{len(telemetry_payload)}-byte telemetry to "
        f"{args.telemetry_host}:{args.telemetry_port}..."
    )

    last_arrival: float | None = None
    next_telemetry = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            if now >= next_telemetry:
                battery_level = random.randint(0, 100)
                telemetry_payload = encode_receive_packet(battery_level)
                telemetry_sock.sendto(
                    telemetry_payload,
                    (args.telemetry_host, args.telemetry_port),
                )
                print(f"Sent receive packet: battery_level={battery_level}%")
                next_telemetry = now + args.telemetry_interval

            try:
                data, address = sock.recvfrom(1024)
            except TimeoutError:
                continue

            arrived_at = time.perf_counter()
            if len(data) != expected_size or not data.startswith(header):
                print(f"Rejected {len(data)}-byte packet from {address}")
                continue

            values = packet_struct.unpack(data[len(header) :])
            interval_ms = (
                None if last_arrival is None else (arrived_at - last_arrival) * 1000.0
            )
            last_arrival = arrived_at
            decoded = dict(zip(field_names, values))
            timing = "first packet" if interval_ms is None else f"{interval_ms:.2f} ms"
            print(f"{address} | {timing} | {decoded}")
    finally:
        sock.close()
        telemetry_sock.close()


if __name__ == "__main__":
    main()
