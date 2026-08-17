import json
import socket
import struct
import time
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


def load_packet_layout() -> tuple[bytes, struct.Struct, list[str]]:
    with SCHEMA_PATH.open(encoding="utf-8") as schema_file:
        command = json.load(schema_file)["packet_types"]["command"]

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


def main() -> None:
    header, packet_struct, field_names = load_packet_layout()
    expected_size = len(header) + packet_struct.size
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    print(f"Listening for {expected_size}-byte UDP commands on {UDP_IP}:{UDP_PORT}...")

    last_arrival: float | None = None
    try:
        while True:
            data, address = sock.recvfrom(1024)
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


if __name__ == "__main__":
    main()
