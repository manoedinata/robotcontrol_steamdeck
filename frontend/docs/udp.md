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

To add a command value, add it to the frontend packet state and to the ordered schema fields. No WebSocket dispatcher or encoder changes should be necessary.

UDP receive telemetry, battery state, RTT, and loss are not implemented in the current backend contract.
