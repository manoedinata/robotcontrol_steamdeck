import argparse
import json
import socket
import struct
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


def encode_battery_level(battery_level: int) -> bytes:
    with SCHEMA_PATH.open(encoding="utf-8") as schema_file:
        telemetry = json.load(schema_file)["packet_types"]["receive"]

    fields = telemetry["fields"]
    if [field["name"] for field in fields] != ["battery_level"]:
        raise ValueError("Simulator requires one telemetry field named battery_level")
    field = fields[0]
    if not field.get("min", 0) <= battery_level <= field.get("max", 100):
        raise ValueError("battery level is outside schema bounds")

    byte_order = {"little": "<", "big": ">"}[telemetry["byte_order"]]
    packet_struct = struct.Struct(byte_order + WIRE_FORMATS[field["type"]])
    return telemetry["header"].encode("ascii") + packet_struct.pack(battery_level)


def main() -> None:
    parser = argparse.ArgumentParser(description="Send one robot battery packet")
    parser.add_argument("battery_level", type=int, help="battery percentage (0..100)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8889)
    args = parser.parse_args()

    payload = encode_battery_level(args.battery_level)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(payload, (args.host, args.port))
    print(
        f"Sent battery_level={args.battery_level}% to {args.host}:{args.port} "
        f"({len(payload)} bytes)"
    )


if __name__ == "__main__":
    main()
