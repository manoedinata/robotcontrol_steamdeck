# Backend Guidance

## Scope

This directory is the sole owner of UDP and camera transport, for every source kind. Electron is only a UI shell and settings store; Vue communicates with this backend through local WebSocket and HTTP endpoints. Direct camera WebSocket sources are listed in `camera_streams` alongside RTSP ones: `CameraWebSocketSource.py` holds one connection per camera and re-serves it at `GET /camera/<id>/stream`, which the camera backend opens like any other source. The renderer never connects to a camera.

## Structure

- `server.py`: FastAPI lifecycle, typed controls/telemetry WebSocket, UDP sender/receiver, and WebRTC signaling endpoint.
- `Recorder.py`: records every configured source at once, one stream-copying ffmpeg each, into Matroska. WebSocket sources are recorded through the relay with `?preroll=1`, so the file starts at the keyframe the hub still holds rather than at the camera's next one. The source list is frozen at start so config churn cannot split a recording; a source that has written video is retried all session, one that never did gives up. Never log or broadcast a camera URL from here.
- `WebRTCStream.py`: selectable go2rtc/aiortc RTSP-to-WebRTC backends, multi-stream registration, and lifecycle cleanup. The aiortc backend keeps one refcounted connection per source, shared by every peer and by the relay; it is released when the last holder goes, so a recording keeps a camera open after the last viewer leaves.
- `PTZController.py`: Hikvision ISAPI pan/tilt/zoom/focus continuous-move requests and value normalizers.
- `RecordingLibrary.py`: reads recordings back -- the session scan, the `session.json` manifest schema's reader half, path containment, ffprobe, and the playback and thumbnail ffmpeg commands. It imports from `Recorder.py` and never the other way round, and it never writes into the recordings folder except for an explicit delete.
- `settings.py`: mutable runtime destination and camera configuration.
- `utils.py`: schema-derived packet defaults, validation, timing, binary encoding, and decoding.
- `test_utils.py`: focused tests for binary layout and validation.
- `test_camera_backend.py`: tests for camera-backend selection, `camera_streams` validation, and PTZ normalizers.
- `test_recording_library.py`: tests for the manifest schema, the filename fallback, path containment, and the playback and thumbnail arguments.
- `../packets-schema.json`: authoritative ordered UDP command schema.
- `../scripts/udp_server_simulation.py`: schema-driven local UDP receiver.

## Runtime Contracts

- `WS /ws/controls` accepts only `config`, `send`, `ptz`, and `record` message types.
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
- The relay serves every source the backend holds a connection to: WebSocket cameras always, and RTSP cameras when `camera_backend` is `aiortc`, which keeps one shared `MediaPlayer` per source in this process. Recording reads the relay for those, so it costs no second camera session. Under `go2rtc` the connection lives in a child process out of reach, so RTSP recordings dial the camera themselves.
- The aiortc backend forwards H.264 packets without decoding them (`decode=False`) and pins the peer to H.264, because forwarding pre-encoded data only works if the negotiated codec is the one the packets are already in. MJPEG is still decoded and re-encoded: no browser accepts it over WebRTC. RTSP carries parameter sets in the SDP, so they are prepended to each keyframe on the way out.
- `GET /camera/<id>/stream` takes `?preroll=1`, which replays the payloads since the camera's last keyframe before the live ones. Recording needs it: a stream copy cannot start anywhere but a keyframe, so without it the front of every file is missing. Never turn it on for the live path, which would then open on stale video and race to catch up.
- Camera sources are RTSP URLs; an empty or absent `camera_streams` list keeps playback idle. `POST /offer` takes `?src=<stream id>` to pick a stream (optional only when one stream is configured).
- FastAPI owns camera transport and keeps every configured RTSP source connected at once so the UI switches without a reconnect. The go2rtc backend manages one localhost-only child process and one named stream per source; the aiortc backend creates one RTSP media player per WebRTC offer. On a config change close only the peers whose stream id or url changed; close everything on backend change and shutdown. Do not silently fall back between selected backends.
- Every session directory carries a `session.json` manifest, written at start, on each part rollover, and at stop -- never on a timer, because the card is usually SD. It records which sources the session contained and what became of each, which the filenames cannot: a source that received no video has its empty file deleted. Like every recording payload it must never contain a camera URL.
- A missing or unparseable manifest is not an error. The session is listed from its directory name and filenames instead, reported as `manifest: false`, with what cannot be known reading as `null` or `unknown`.
- The library covers only the folder the record button currently writes to, resolved per request through `Recorder.recordings_root()`. `GET /storage/targets` is the picker for everything else.
- An unavailable recordings root is a `200` with `available: false`, not an error: an absent card is an answer the UI renders.
- `GET /recordings` never probes. One ffprobe per file would fork hundreds of processes on a card with hundreds of sessions; only the opened session is probed, bounded and cached.
- `GET /recordings/<session>/<file>/play` is a live remux to fragmented MP4. It carries no index and honours no Range; seeking is `?t=`. A codec the renderer cannot decode answers `415` rather than playing as a black rectangle, and `?transcode=1` is the explicit opt-in.
- Nothing is ever written into the recordings folder on a read path. Thumbnails and probe results are cached elsewhere, so the rule about never creating paths under `/run/media` stays trivially true.
- `DELETE /recordings/<session>` refuses the running session, checked against the live recorder rather than the manifest.
- Run one Uvicorn worker because runtime state is process-local.
- Container builds set `PYTHONPATH=/app/backend`; do not rely on the working directory for backend module imports.

## Extension Pattern

To add a command field, update `../packets-schema.json` and initialize/bind the same field in the frontend `useControlState.js`. Generic validation, merge, and encoding code should remain unchanged.

## Validation

After Python changes, run `test_utils`, check syntax and Pylance diagnostics for `backend/` and `scripts/`, and verify the frontend build when contracts change. Static validation must not require a live camera, backend server, browser, or UDP peer; interactive validation belongs to the user.

## Documentation

Update `README.md` and this file when endpoints, message types, packet fields, transport ownership, camera behavior, or limitations change. Keep frontend architecture documentation synchronized with the same contract.
