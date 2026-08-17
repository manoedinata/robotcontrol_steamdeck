import math
import struct
from typing import Any

WIRE_TYPES = {
    "int8": ("b", 1, int),
    "uint8": ("B", 1, int),
    "int16": ("h", 2, int),
    "uint16": ("H", 2, int),
    "int32": ("i", 4, int),
    "uint32": ("I", 4, int),
    "float32": ("f", 4, float),
    "float64": ("d", 8, float),
}


def s_to_hz(s: float) -> float:
    """Convert seconds to Hertz (Hz)."""
    if s <= 0:
        return 0.0
    return 1.0 / s


def hz_to_s(hz: float) -> float:
    """Convert Hertz (Hz) to seconds."""
    if hz <= 0:
        return 0.0
    return 1.0 / hz


def hz_to_ms(hz: float) -> float:
    """Convert Hertz (Hz) to milliseconds."""
    return hz_to_s(hz) * 1000.0


def generate_default_state(schema: dict) -> dict:
    """Return the default state for a binary packet schema."""
    fields = schema["packet_types"]["command"]["fields"]
    return {
        field["name"]: field.get("default", default_for_wire_type(field["type"]))
        for field in fields
    }


def command_schema(schema: dict) -> dict:
    return schema["packet_types"]["command"]


def default_for_wire_type(wire_type: str) -> int | float:
    if wire_type not in WIRE_TYPES:
        raise ValueError(f"Unsupported wire type: {wire_type}")
    return 0.0 if wire_type.startswith("float") else 0


def validate_packet_values(packet: dict[str, Any], schema: dict) -> None:
    """Validate names, primitive types, and numeric ranges from the schema."""
    fields = {field["name"]: field for field in command_schema(schema)["fields"]}
    unknown = set(packet) - set(fields)
    if unknown:
        raise ValueError(f"Unknown packet fields: {sorted(unknown)}")

    for name, value in packet.items():
        field = fields[name]
        wire_type = field["type"]
        if wire_type not in WIRE_TYPES:
            raise ValueError(f"Unsupported wire type: {wire_type}")
        expected_type = WIRE_TYPES[wire_type][2]
        valid_type = (
            isinstance(value, (int, float))
            if expected_type is float
            else isinstance(value, int)
        )
        if isinstance(value, bool) or not valid_type:
            type_name = "number" if expected_type is float else "integer"
            raise ValueError(f"Field {name!r} must be a {type_name}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"Field {name!r} must be finite")
        if "min" in field and value < field["min"]:
            raise ValueError(f"Field {name!r} is below its minimum")
        if "max" in field and value > field["max"]:
            raise ValueError(f"Field {name!r} exceeds its maximum")


def encode_binary_packet(packet: dict[str, Any], schema: dict) -> bytes:
    """Encode a complete command packet using schema order and byte order."""
    packet_schema = command_schema(schema)
    validate_packet_values(packet, schema)
    byte_order = {"little": "<", "big": ">"}[packet_schema["byte_order"]]
    payload = bytearray(packet_schema["header"].encode("ascii"))

    for field in packet_schema["fields"]:
        name = field["name"]
        if name not in packet:
            raise ValueError(f"Missing packet field: {name}")
        format_code = WIRE_TYPES[field["type"]][0]
        try:
            payload.extend(struct.pack(f"{byte_order}{format_code}", packet[name]))
        except struct.error as error:
            raise ValueError(f"Could not encode field {name!r}: {error}") from error

    return bytes(payload)
