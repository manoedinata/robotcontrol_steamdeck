# Backend Guidance

## Scope

This directory is the sole owner of UDP and camera transport. Electron is only a UI shell and settings store; Vue communicates with this backend through local WebSocket and HTTP endpoints.

## Structure

- `server.py`: FastAPI lifecycle, typed controls/telemetry WebSocket, UDP sender/receiver, and WebRTC signaling endpoint.
- `WebRTCStream.py`: per-peer RTSP media players and WebRTC lifecycle cleanup.
- `settings.py`: mutable runtime destination and camera configuration.
- `utils.py`: schema-derived packet defaults, validation, timing, binary encoding, and decoding.
- `test_utils.py`: focused tests for binary layout and validation.
- `../packets-schema.json`: authoritative ordered UDP command schema.
- `../scripts/udp_server_simulation.py`: schema-driven local UDP receiver.

## Runtime Contracts

- `WS /ws/controls` accepts only `config` and `control` message types.
- `GET /health` is a lightweight readiness probe. Keep it dependency-free (no camera connect, no UDP peer) so the Docker entrypoint can poll it safely.
- Config fields are `udp_host`, `udp_port`, and `camera_url`; they never enter UDP payloads. RTSP credentials are URL-encoded in `camera_url` userinfo and must never be logged.
- `udp_listen_port` configures the independent telemetry socket, defaults to `8889`, and binds on all interfaces.
- Empty UDP host plus port `0` disables transmission. Any partially configured destination is invalid.
- Control messages may contain any subset of fields declared in `packets-schema.json`; merge them into the current complete packet.
- Never add field-specific WebSocket handlers or hard-coded binary offsets. Field order, defaults, types, and bounds come from the schema.
- Supported wire types are `int8`, `uint8`, `int16`, `uint16`, `int32`, `uint32`, `float32`, and `float64`.
- Send cached binary UDP bytes at 50 Hz only while a controls WebSocket is connected and UDP is enabled.
- Reset controls to schema defaults after the final controls WebSocket disconnects.
- Decode exact telemetry datagrams from `packet_types.receive` and broadcast `{ "type": "receive", "packet": { ... } }` to every connected UI.
- Periodically measure ICMP latency to the configured UDP destination and broadcast `{ "type": "ping", "ping_ms": number | null }` to connected UIs. This is host reachability, not command acknowledgement RTT.
- Camera sources are RTSP URLs; an empty `camera_url` keeps playback idle.
- Create and clean up one aiortc RTSP media player per WebRTC offer. Close all peers on URL changes and shutdown.
- Run one Uvicorn worker because runtime state is process-local.
- Container builds set `PYTHONPATH=/app/backend`; do not rely on the working directory for backend module imports.

## Extension Pattern

To add a command field, update `../packets-schema.json` and initialize/bind the same field in the frontend `useControlState.js`. Generic validation, merge, and encoding code should remain unchanged.

## Validation

After Python changes, run `test_utils`, check syntax and Pylance diagnostics for `backend/` and `scripts/`, and verify the frontend build when contracts change. Static validation must not require a live camera, backend server, browser, or UDP peer; interactive validation belongs to the user.

## Documentation

Update `README.md` and this file when endpoints, message types, packet fields, transport ownership, camera behavior, or limitations change. Keep frontend architecture documentation synchronized with the same contract.
