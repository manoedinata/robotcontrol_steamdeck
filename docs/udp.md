# UDP Control Protocol

## Command Scheduling

While Home is mounted and a valid destination is configured, the renderer publishes the latest `[yVelocity, thetaVelocity]` through `window.electronAPI.updateUdpVelocity(host, port, velocity)`. The Electron main process sends that state every 20 ms (50 Hz), without overlapping sends.

Stopping Home, navigating away, closing the renderer, or quitting Electron clears the scheduler. The application sends no final zero-velocity packet; the receiving STM32 must stop the wheels through its UDP receive-timeout watchdog.

## Command Packets

Each command is exactly 11 bytes:

| Offset | Size | Encoding                          | Value          |
| ------ | ---- | --------------------------------- | -------------- |
| `0`    | 3    | ASCII bytes                       | Header `ITS`   |
| `3`    | 4    | IEEE-754 `float32`, little-endian | Y velocity     |
| `7`    | 4    | IEEE-754 `float32`, little-endian | Theta velocity |

`electron-components/udp-client.js` validates the destination, header, array shape, finite values, and float32 range before sending.

## Battery Replies

A peer may reply from the command socket with exactly 7 bytes:

| Offset | Size | Encoding                      | Value                       |
| ------ | ---- | ----------------------------- | --------------------------- |
| `0`    | 3    | ASCII bytes                   | Header `ITS`                |
| `3`    | 4    | Signed `int32`, little-endian | Battery percentage `0..100` |

Only valid replies from the configured destination host and port are forwarded to Home telemetry. Battery telemetry demonstrates that the configured peer replied; it does not acknowledge a particular velocity command or guarantee delivery.

## Peer Health Metrics

The UDP telemetry reports `RX <count>/s · RTT ~<ms> ms · Loss ~<percent>%`. RX is the exact number of valid configured-peer replies received during the trailing second. The main process marks the peer connected when a valid reply arrived within the last second, waiting before the first reply, and disconnected when replies become stale.

RTT and loss are approximate because the current packets contain no sequence number. Replies are paired in arrival order with the oldest unmatched recent send. RTT averages the latest 20 such pairs; loss compares independent reply and settled-send counts over a trailing five-second window, with a 250 ms grace period for in-flight replies. Destination changes and transmission stops clear all measurements. Exact per-command RTT and loss require a sequence ID echoed by the receiver.

## Development Diagnostic Peer

`udp-server.js` listens on UDP port `41234`, rejects malformed packets, decodes valid commands, reports timing, and replies with the Linux system battery percentage when readable. It is started by `npm run dev` and is not part of the production robot control path.
