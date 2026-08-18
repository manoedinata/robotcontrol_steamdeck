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


def packet_schema(schema: dict, packet_type: str = "command") -> dict:
    try:
        return schema["packet_types"][packet_type]
    except KeyError as error:
        raise ValueError(f"Unknown packet type: {packet_type}") from error


def generate_default_state(schema: dict, packet_type: str = "command") -> dict:
    """Return the default state for a binary packet schema."""
    fields = packet_schema(schema, packet_type)["fields"]
    return {
        field["name"]: field.get("default", default_for_wire_type(field["type"]))
        for field in fields
    }


def default_for_wire_type(wire_type: str) -> int | float:
    if wire_type not in WIRE_TYPES:
        raise ValueError(f"Unsupported wire type: {wire_type}")
    return 0.0 if wire_type.startswith("float") else 0


def validate_packet_values(
    packet: dict[str, Any], schema: dict, packet_type: str = "command"
) -> None:
    """Validate names, primitive types, and numeric ranges from the schema."""
    fields = {
        field["name"]: field for field in packet_schema(schema, packet_type)["fields"]
    }
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


def packet_struct(schema: dict, packet_type: str = "command") -> struct.Struct:
    definition = packet_schema(schema, packet_type)
    try:
        byte_order = {"little": "<", "big": ">"}[definition["byte_order"]]
        format_codes = "".join(
            WIRE_TYPES[field["type"]][0] for field in definition["fields"]
        )
    except KeyError as error:
        raise ValueError(f"Invalid {packet_type!r} packet schema: {error}") from error
    return struct.Struct(byte_order + format_codes)


def encode_binary_packet(
    packet: dict[str, Any], schema: dict, packet_type: str = "command"
) -> bytes:
    """Encode a complete packet using schema order and byte order."""
    definition = packet_schema(schema, packet_type)
    validate_packet_values(packet, schema, packet_type)
    payload = bytearray(definition["header"].encode("ascii"))
    value_struct = packet_struct(schema, packet_type)

    values = []
    for field in definition["fields"]:
        name = field["name"]
        if name not in packet:
            raise ValueError(f"Missing packet field: {name}")
        values.append(packet[name])

    try:
        payload.extend(value_struct.pack(*values))
    except struct.error as error:
        raise ValueError(f"Could not encode {packet_type!r} packet: {error}") from error

    return bytes(payload)


def decode_binary_packet(
    payload: bytes, schema: dict, packet_type: str
) -> dict[str, int | float]:
    """Decode a packet only when its header and total length match the schema."""
    definition = packet_schema(schema, packet_type)
    try:
        header = definition["header"].encode("ascii")
    except (AttributeError, UnicodeEncodeError) as error:
        raise ValueError(f"Invalid {packet_type!r} packet header") from error

    value_struct = packet_struct(schema, packet_type)
    expected_size = len(header) + value_struct.size
    if len(payload) != expected_size:
        raise ValueError(
            f"Invalid {packet_type!r} packet length: expected {expected_size}, "
            f"received {len(payload)}"
        )
    if not payload.startswith(header):
        raise ValueError(f"Invalid {packet_type!r} packet header")

    try:
        values = value_struct.unpack(payload[len(header) :])
    except struct.error as error:
        raise ValueError(f"Could not decode {packet_type!r} packet: {error}") from error

    packet = dict(
        zip((field["name"] for field in definition["fields"]), values, strict=True)
    )
    validate_packet_values(packet, schema, packet_type)
    return packet
