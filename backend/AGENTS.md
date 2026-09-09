# Backend Guidance

## Scope

This directory is the sole owner of UDP and camera transport, for every source kind. Electron is only a UI shell and settings store; Vue communicates with this backend through local WebSocket and HTTP endpoints. Direct camera WebSocket sources are listed in `camera_streams` alongside RTSP ones: `CameraWebSocketSource.py` holds one connection per camera and re-serves it at `GET /camera/<id>/stream`, which the camera backend opens like any other source. The renderer never connects to a camera.

## Structure

- `server.py`: FastAPI lifecycle, typed controls/telemetry WebSocket, UDP sender/receiver, and WebRTC signaling endpoint.
- `Recorder.py`: records every configured source at once, one stream-copying ffmpeg each, into Matroska. The source list is frozen at start so config churn cannot split a recording; a source that has written video is retried all session, one that never did gives up. Never log or broadcast a camera URL from here.
- `WebRTCStream.py`: selectable go2rtc/aiortc RTSP-to-WebRTC backends, multi-stream registration, and lifecycle cleanup.
- `PTZController.py`: Hikvision ISAPI pan/tilt/zoom/focus continuous-move requests and value normalizers.
- `settings.py`: mutable runtime destination and camera configuration.
- `utils.py`: schema-derived packet defaults, validation, timing, binary encoding, and decoding.
- `test_utils.py`: focused tests for binary layout and validation.
- `test_camera_backend.py`: tests for camera-backend selection, `camera_streams` validation, and PTZ normalizers.
- `../packets-schema.json`: authoritative ordered UDP command schema.
- `../scripts/udp_server_simulation.py`: schema-driven local UDP receiver.

## Runtime Contracts

- `WS /ws/controls` accepts only `config`, `send`, and `ptz` message types.
- `GET /health` is a lightweight readiness probe. Keep it dependency-free (no camera connect, no UDP peer) so the Docker entrypoint can poll it safely.
- Config fields include `udp_host`, `udp_port`, `udp_listen_port`, `camera_streams`, and `camera_backend`; they never enter UDP payloads. `camera_backend` is `go2rtc` by default or `aiortc`. `camera_streams` is a list of `{ "id", "url" }` RTSP sources kept warm at once; ids match `[A-Za-z0-9_-]{1,64}` and are unique. RTSP credentials are URL-encoded in each `url` userinfo and must never be logged.
- `udp_listen_port` configures the independent telemetry socket, defaults to `8889`, and binds on all interfaces.
- Empty UDP host plus port `0` disables transmission. Any partially configured destination is invalid.
- Control messages may contain any subset of fields declared in `packets-schema.json`; merge them into the current complete packet.
- Never add field-specific WebSocket handlers or hard-coded binary offsets. Field order, defaults, types, and bounds come from the schema.
- Supported wire types are `int8`, `uint8`, `int16`, `uint16`, `int32`, `uint32`, `float32`, and `float64`.
- Send cached binary UDP bytes at 50 Hz only while a controls WebSocket is connected and UDP is enabled.
- Reset controls to schema defaults after the final controls WebSocket disconnects.
- Announce the settable send fields to each UI on WebSocket connect as `{ "type": "schema", "fields": [...] }`, derived from `packet_types.send` with padding roles removed. The renderer must never read `packets-schema.json` itself.
- Decode exact telemetry datagrams from `packet_types.receive` and broadcast `{ "type": "receive", "packet": { ... } }` to every connected UI.
- Periodically measure ICMP latency to the configured UDP destination and broadcast `{ "type": "ping", "ping_ms": number | null }` to connected UIs. This is host reachability, not command acknowledgement RTT.
- Camera sources are RTSP URLs; an empty or absent `camera_streams` list keeps playback idle. `POST /offer` takes `?src=<stream id>` to pick a stream (optional only when one stream is configured).
- FastAPI owns camera transport and keeps every configured RTSP source connected at once so the UI switches without a reconnect. The go2rtc backend manages one localhost-only child process and one named stream per source; the aiortc backend creates one RTSP media player per WebRTC offer. On a config change close only the peers whose stream id or url changed; close everything on backend change and shutdown. Do not silently fall back between selected backends.
- Run one Uvicorn worker because runtime state is process-local.
- Container builds set `PYTHONPATH=/app/backend`; do not rely on the working directory for backend module imports.

## Extension Pattern

To add a command field, update `../packets-schema.json` and initialize/bind the same field in the frontend `useControlState.js`. Generic validation, merge, and encoding code should remain unchanged.

## Validation

After Python changes, run `test_utils`, check syntax and Pylance diagnostics for `backend/` and `scripts/`, and verify the frontend build when contracts change. Static validation must not require a live camera, backend server, browser, or UDP peer; interactive validation belongs to the user.

## Documentation

Update `README.md` and this file when endpoints, message types, packet fields, transport ownership, camera behavior, or limitations change. Keep frontend architecture documentation synchronized with the same contract.
