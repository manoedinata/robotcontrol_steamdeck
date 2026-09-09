import asyncio
import math
import struct
import threading
from collections.abc import Callable
from contextlib import suppress
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


def run_detached(
    func: Callable[..., Any], *args: Any, thread_name: str | None = None
) -> asyncio.Future:
    """Await a blocking call on a daemon thread that shutdown never joins.

    asyncio.to_thread() and run_in_executor(None, ...) both hand work to the
    loop's default executor, and closing the loop waits for every thread in
    it. A blocking network call with a multi-second timeout would then hold
    the process open long after the UI is gone -- quitting mid-reconnect kept
    the RTSP dial, and the stream with it, alive until ffmpeg gave up. On a
    daemon thread the awaiting coroutine can be abandoned and the interpreter
    can exit while the call is still outstanding.

    The returned future can be awaited, cancelled, or waited on alongside
    other awaitables; cancelling it abandons the call rather than stopping it.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def deliver(setter: Callable[[Any], None], value: Any) -> None:
        # The awaiting side may be gone by the time the call lands.
        if not future.cancelled():
            setter(value)

    def runner() -> None:
        try:
            result = func(*args)
        except BaseException as error:  # delivered to the awaiting coroutine
            setter, value = future.set_exception, error
        else:
            setter, value = future.set_result, result
        # RuntimeError here means the loop is already closed, which is exactly
        # the case this helper exists to survive.
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(deliver, setter, value)

    name = thread_name or f"detached-{getattr(func, '__name__', 'call')}"
    threading.Thread(target=runner, name=name, daemon=True).start()
    return future


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


def packet_schema(schema: dict, packet_type: str = "send") -> dict:
    try:
        return schema["packet_types"][packet_type]
    except KeyError as error:
        raise ValueError(f"Unknown packet type: {packet_type}") from error


def generate_default_state(schema: dict, packet_type: str = "send") -> dict:
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


def _field_count(field: dict[str, Any]) -> int:
    count = field.get("count")
    return int(count) if count is not None else 1


def _expand_field(field: dict[str, Any]) -> list[dict[str, Any]]:
    if "count" in field:
        return [field] * field["count"]
    return [field]


def validate_packet_values(
    packet: dict[str, Any], schema: dict, packet_type: str = "send"
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
        count = _field_count(field)
        if wire_type not in WIRE_TYPES:
            raise ValueError(f"Unsupported wire type: {wire_type}")
        expected_type = WIRE_TYPES[wire_type][2]
        values_to_check = value if count > 1 and isinstance(value, list) else [value]
        for item in values_to_check:
            valid_type = (
                isinstance(item, (int, float))
                if expected_type is float
                else isinstance(item, int)
            )
            if isinstance(item, bool) or not valid_type:
                type_name = "number" if expected_type is float else "integer"
                raise ValueError(f"Field {name!r} must be a {type_name}")
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError(f"Field {name!r} must be finite")
            if "min" in field and item < field["min"]:
                raise ValueError(f"Field {name!r} is below its minimum")
            if "max" in field and item > field["max"]:
                raise ValueError(f"Field {name!r} exceeds its maximum")


def slew_rates(schema: dict, packet_type: str = "send") -> dict[str, float]:
    """Per-field ramp rates, in packet units per second.

    A field declares `slew_rate` to cap how fast its command may change on the
    wire, so a joystick slammed to full deflection becomes a linear ramp
    instead of a step the robot has to absorb. Fields that leave it out (and
    padding, which carries no command) are sent through unchanged, as is a
    rate of 0.
    """
    rates: dict[str, float] = {}
    for field in packet_schema(schema, packet_type)["fields"]:
        if field.get("role") == "padding":
            continue
        rate = field.get("slew_rate")
        if rate is None:
            continue
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            raise ValueError(f"Field {field['name']!r} slew_rate must be a number")
        if not math.isfinite(rate) or rate < 0:
            raise ValueError(
                f"Field {field['name']!r} slew_rate must be finite and not negative"
            )
        if rate > 0:
            rates[field["name"]] = float(rate)
    return rates


def slew_step(value: float, target: float, rate: float | None, dt: float) -> float:
    """Move `value` toward `target` by at most `rate * dt`.

    This is the trapezoidal profile: capping the per-second change makes the
    command ramp linearly, symmetrically in both directions. Without a rate
    there is nothing to limit and the target applies immediately.
    """
    if rate is None or rate <= 0:
        return target
    if dt <= 0:
        # No time has passed, so nothing may move. This is not the same as
        # having no rate: returning the target here would teleport past the
        # ramp on any tick the clock did not advance.
        return value
    step = rate * dt
    delta = target - value
    if delta > step:
        return value + step
    if delta < -step:
        return value - step
    return target


def packet_header(schema: dict, packet_type: str = "send") -> bytes:
    """ASCII header bytes for a packet type; empty when the schema omits one.

    A packet type may leave `header` out, null, or `""` to put its fields on
    the wire on their own. Encoding then writes no header bytes and decoding
    matches on length alone.
    """
    header = packet_schema(schema, packet_type).get("header") or ""
    if not isinstance(header, str):
        raise ValueError(f"Invalid {packet_type!r} packet header")
    try:
        return header.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError(f"Invalid {packet_type!r} packet header") from error


def packet_struct(schema: dict, packet_type: str = "send") -> struct.Struct:
    definition = packet_schema(schema, packet_type)
    try:
        byte_order = {"little": "<", "big": ">"}[definition["byte_order"]]
        format_codes = "".join(
            WIRE_TYPES[field["type"]][0]
            for field in definition["fields"]
            for _ in range(_field_count(field))
        )
    except KeyError as error:
        raise ValueError(f"Invalid {packet_type!r} packet schema: {error}") from error
    return struct.Struct(byte_order + format_codes)


def do_additional_step_before_sending(packet_field: str) -> Any:
    processed_packet = packet_field

    # if packet_field == "pwm":
    #     # Example processing for "pwm" field
    #     processed_packet = float(packet_field)  # Convert to float if needed

    return processed_packet


def encode_binary_packet(
    packet: dict[str, Any], schema: dict, packet_type: str = "send"
) -> bytes:
    """Encode a complete packet using schema order and byte order."""
    definition = packet_schema(schema, packet_type)
    validate_packet_values(packet, schema, packet_type)
    payload = bytearray(packet_header(schema, packet_type))
    value_struct = packet_struct(schema, packet_type)

    values = []
    for field in definition["fields"]:
        name = field["name"]
        count = _field_count(field)
        if name not in packet:
            default = field.get("default", default_for_wire_type(field["type"]))
            values.extend([default] * count)
        else:
            value = packet[name]
            value = do_additional_step_before_sending(value)
            if count > 1:
                if isinstance(value, list):
                    if len(value) != count:
                        raise ValueError(
                            f"Field {name!r} must be a list of length {count}"
                        )
                    values.extend(value)
                else:
                    values.extend([value] * count)
            else:
                values.append(value)

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
    header = packet_header(schema, packet_type)

    value_struct = packet_struct(schema, packet_type)
    expected_size = len(header) + value_struct.size
    if len(payload) != expected_size:
        raise ValueError(
            f"Invalid {packet_type!r} packet length: expected {expected_size}, "
            f"received {len(payload)}"
        )
    if header and not payload.startswith(header):
        raise ValueError(f"Invalid {packet_type!r} packet header")

    try:
        values = value_struct.unpack(payload[len(header) :])
    except struct.error as error:
        raise ValueError(f"Could not decode {packet_type!r} packet: {error}") from error

    decoded: dict[str, Any] = {}
    index = 0
    for field in definition["fields"]:
        count = _field_count(field)
        name = field["name"]
        if count > 1:
            decoded[name] = list(values[index : index + count])
        else:
            decoded[name] = values[index]
        index += count

    validate_packet_values(decoded, schema, packet_type)
    return decoded
