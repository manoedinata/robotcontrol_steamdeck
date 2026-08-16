def ms_to_hz(ms: float) -> float:
    """Convert milliseconds to Hertz (Hz)."""
    if ms <= 0:
        return 0.0
    return 1.0 / ms


def hz_to_ms(hz: float) -> float:
    """Convert Hertz (Hz) to milliseconds."""
    if hz <= 0:
        return 0.0
    return 1.0 / hz


def generate_default_state(schema: dict) -> dict:
    """Creates a default dictionary based on the JSON Schema properties."""
    state = {}
    properties = schema.get("properties", {})

    for key, prop in properties.items():
        prop_type = prop.get("type")
        if prop_type == "number":
            state[key] = 0.0
        elif prop_type == "integer":
            state[key] = 0
        elif prop_type == "boolean":
            state[key] = False
        elif prop_type == "string":
            state[key] = ""
        else:
            state[key] = None
    return state
