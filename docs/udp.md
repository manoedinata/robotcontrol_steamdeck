# UDP Control Protocol

## Command Scheduling

While Home is mounted and a valid destination is configured, the renderer publishes the latest `[yVelocity, thetaVelocity]` through `window.electronAPI.updateUdpVelocity(host, port, velocity)`. The Electron main process sends that state every 20 ms (50 Hz), without overlapping sends.

Stopping Home, navigating away, closing the renderer, or quitting Electron clears the scheduler. The application sends no final zero-velocity packet; the receiving STM32 must stop the wheels through its UDP receive-timeout watchdog.

## Configurable Packet Schema

The command and reply packet layouts are defined in `packet-schema.json` at the
repository root. A single JSON file describes both packets so the sender
(`electron-components/udp-client.js`), the 50 Hz main-process scheduler, and the
development peer (`udp-server.js`) always agree on byte order, field order,
field sizes, and headers. `electron-components/packet-schema.js` compiles this
schema into a codec: byte offsets are derived from field order, so you never
hand-maintain offsets.

```json
{
  "byteOrder": "little",
  "command": {
    "header": "ITS",
    "fields": [
      { "name": "yVelocity", "type": "float32", "role": "yVelocity", "default": 0 },
      { "name": "thetaVelocity", "type": "float32", "role": "thetaVelocity", "default": 0 }
    ]
  },
  "reply": {
    "header": "ITS",
    "fields": [
      { "name": "batteryPercentage", "type": "int32", "role": "batteryPercentage", "min": 0, "max": 100, "default": 0 }
    ]
  }
}
```

Field attributes:

- `name`: unique field identifier within the packet.
- `type`: one of `float32`, `float64`, `int8`, `uint8`, `int16`, `uint16`, `int32`, `uint32`.
- `role` (optional): a semantic tag the app uses to locate well-known values. The recognized roles are `yVelocity` and `thetaVelocity` for commands and `batteryPercentage` for replies. Fields without a matching role are still encoded/decoded but are not consumed by the current UI.
- `default` (optional): value written when the app does not supply the field (defaults to `0`). New command fields you add are sent with this default until wired to a source.
- `min` / `max` (optional): inclusive decode bounds. A reply whose value falls outside the range is rejected, matching the previous `0..100` battery guard.

`byteOrder` accepts `little` (default) or `big`; single-byte integer types ignore it.

To change the protocol, edit `packet-schema.json` and restart the app and the
diagnostic peer (the schema is read at startup). Adding a field, changing a
type, or reordering fields requires no code changes. If the file is missing, the
built-in default matches the historical layout below; malformed JSON or an
invalid schema fails fast at startup.

### Default Command Packet

With the shipped schema each command is exactly 11 bytes:

| Offset | Size | Encoding                          | Value          |
| ------ | ---- | --------------------------------- | -------------- |
| `0`    | 3    | ASCII bytes                       | Header `ITS`   |
| `3`    | 4    | IEEE-754 `float32`, little-endian | Y velocity     |
| `7`    | 4    | IEEE-754 `float32`, little-endian | Theta velocity |

`electron-components/udp-client.js` validates the destination, then the codec
validates the header, field values, and numeric ranges before sending.

### Default Battery Telemetry

A peer may send battery telemetry from the command socket with exactly 7 bytes:

| Offset | Size | Encoding                      | Value                       |
| ------ | ---- | ----------------------------- | --------------------------- |
| `0`    | 3    | ASCII bytes                   | Header `ITS`                |
| `3`    | 4    | Signed `int32`, little-endian | Battery percentage `0..100` |

Only valid packets from the configured destination host and port are forwarded to Home telemetry. Battery telemetry demonstrates that the configured peer is transmitting; it does not acknowledge a particular velocity command or guarantee delivery.

## Peer Health Metrics

The UDP telemetry reports `RX <count>/s · RTT ~<ms> ms · Loss ~<percent>%`. RX is the exact number of valid configured-peer replies received during the trailing second. The main process marks the peer connected when a valid reply arrived within the last second, waiting before the first reply, and disconnected when replies become stale.

RTT and loss are approximate because the current packets contain no sequence number. Replies are paired in arrival order with the oldest unmatched recent send. RTT averages the latest 20 such pairs; loss compares independent reply and settled-send counts over a trailing five-second window, with a 250 ms grace period for in-flight replies. Destination changes and transmission stops clear all measurements. Exact per-command RTT and loss require a sequence ID echoed by the receiver.

## Development Diagnostic Peer

`udp-server.js` listens on UDP port `41234`, rejects malformed packets, decodes valid commands using the same `packet-schema.json`, and reports timing plus every decoded field. The latest valid command sender becomes its battery telemetry destination. After that destination is known, the server sends the Linux system battery percentage every 20 ms (50 Hz), independently of command arrival; it sends nothing before the first valid command or when no battery is readable. It is started by `npm run dev` and is not part of the production robot control path.
