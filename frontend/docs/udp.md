# WebSocket and UDP Contract

## WebSocket

The renderer connects to `/ws/controls` and sends JSON envelopes separated by `type`.

Configuration:

```json
{
  "type": "config",
  "config": {
    "udp_host": "127.0.0.1",
    "udp_port": 8888,
    "udp_listen_port": 8889,
    "camera_url": "rtsp://camera/stream"
  }
}
```

Control state:

```json
{
  "type": "control",
  "packet": {
    "vy": 1.5,
    "vtheta": -0.25
  }
}
```

Control packets may contain a subset of schema fields. FastAPI validates those fields and merges them into the current complete state. Invalid messages receive a typed error response and do not close the socket. After reconnect, the renderer sends the latest config followed by latest control state.

Robot telemetry uses the same WebSocket in the backend-to-renderer direction:

```json
{"type":"telemetry","packet":{"battery_level":75}}
```

The frontend accepts integer battery values in `0..100`, marks telemetry live on receipt, and marks it stale after two seconds without another valid packet.

## UDP Scheduling

FastAPI caches the encoded command and sends it every 20 ms (50 Hz) while at least one UI WebSocket is connected and a complete UDP destination is enabled. Empty host plus port `0` disables sends. The final UI disconnect resets controls to schema defaults. No final stop datagram is sent; the robot must stop motion through a receive-timeout watchdog.

## Binary Schema

`packets-schema.json` at the repository root declares byte order, ASCII header, ordered fields, defaults, types, and bounds. Supported field types are `int8`, `uint8`, `int16`, `uint16`, `int32`, `uint32`, `float32`, and `float64`. Offsets are derived from field order.

The shipped packet is 11 bytes:

| Offset | Size | Encoding                | Field        |
| ------ | ---- | ----------------------- | ------------ |
| `0`    | 3    | ASCII                   | `ITS` header |
| `3`    | 4    | little-endian `float32` | `vy`         |
| `7`    | 4    | little-endian `float32` | `vtheta`     |

`packet_types.telemetry` defines the independent receive layout. The shipped telemetry datagram is 4 bytes: ASCII `ITS` followed by one `uint8 battery_level` percentage. FastAPI listens on the persisted `udpListenPort` setting (`8889` by default), binds all interfaces, and rebinds when the setting changes. Header and total length must match exactly.

To add a command value, add it to the frontend packet state and to the ordered schema fields. No WebSocket dispatcher or encoder changes should be necessary.

## Extending the UDP packet

The following three edits are enough to wire a new field end-to-end. Do not touch WebSocket dispatch, IPC, or the encoder logic.

### 1. Declare the field in the shared schema

Edit [`packets-schema.json`](../../packets-schema.json). Append a new entry to `packet_types.command.fields` after the existing fields. Choose a name, a wire type, and optional `min`/`max`/`default`.

Supported wire types: `int8`, `uint8`, `int16`, `uint16`, `int32`, `uint32`, `float32`, `float64`.

Example: add a `gripper` clamp value stored as a `uint8` from 0 to 255.

```json
{
  "name": "gripper",
  "type": "uint8",
  "role": "gripper",
  "default": 0,
  "min": 0,
  "max": 255
}
```

Field order matters. The encoder packs fields in the exact order they appear under `fields`, so the robot parser must read the same order. After this change the UDP packet size increases by 1 byte and all previous offsets remain unchanged.

### 2. Initialize the field in the frontend control state

Edit [`frontend/src/composables/useControlState.js`](../../src/composables/useControlState.js). Add the same field name to the `packet` object with the same default.

```javascript
const packet = reactive({
    vy: 0,
    vtheta: 0,
    gripper: 0,
})
```

`resetPacket()` will also zero the new field automatically because it iterates over `Object.keys(packet)`.

### 3. Bind a UI component to the new field

Call `updatePacket({ gripper: value })` from any component. For example, in a new Settings slider or a controller button:

```javascript
const { updatePacket } = useControlState()
updatePacket({ gripper: 180 })
```

The backend receives a `control` message containing the new field, validates it against the schema bounds, merges it into the cached packet, and includes it in the next binary UDP frame. Fields that the frontend omits keep their previous or default values.

### What does not need to change

- `useBackendConnection.js` — it sends the whole `packet` object generically.
- `backend/server.py` — it reads `packet` from the WebSocket and merges it with the current state.
- `backend/utils.py` — `encode_binary_packet` derives byte order, offsets, and types from the schema.
- The WebSocket message shape stays the same: `{ "type": "control", "packet": { ... } }`.

### Validation rules the backend enforces

For each field the backend checks:

- The value type matches the schema type (e.g., integer for integer types, finite number for floats).
- `min <= value <= max` when bounds are declared.
- `NaN` and `Infinity` are rejected for floating-point fields.
- Unknown field names are rejected.

If validation fails, the backend replies with `{ "type": "error", "message": "..." }` and does not update the packet.

Battery telemetry is implemented. Command acknowledgement, sequence IDs, exact RTT, and loss are not implemented.
