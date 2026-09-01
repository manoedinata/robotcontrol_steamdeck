# Steam Deck Robot Monitor Backend

FastAPI owns robot transport and RTSP camera transport for the Steam Deck UI. Vue sends configuration and control state over a local WebSocket; the backend sends commands at 50 Hz, receives battery telemetry on a separate UDP port, and converts RTSP to local WebRTC through the selected camera backend. Direct camera WebSocket H.264 playback bypasses this camera path and sends an empty `camera_url` to keep RTSP idle.

## Endpoints

- `WS /ws/controls`: typed configuration and control messages.
- `POST /offer`: WebRTC SDP signaling for a receive-only video peer.
- `GET /health`: readiness probe used by the Docker entrypoint and external health checks.

Configuration message (RTSP credentials may be supplied as URL-encoded userinfo):

```json
{"type":"config","config":{"udp_host":"127.0.0.1","udp_port":8888,"udp_listen_port":8889,"camera_url":"rtsp://user:password@camera/stream","camera_backend":"go2rtc"}}
```

Control messages may update any subset of schema fields:

```json
{"type":"send","packet":{"vy":1.5,"vtheta":-0.25}}
```

Invalid messages receive `{"type":"error","message":"..."}` without closing the connection. Empty `udp_host` plus port `0` disables UDP. Empty `camera_url` leaves camera capture idle.

Valid robot telemetry is broadcast to all connected UIs:

```json
{"type":"receive","packet":{"battery_level":75}}
```

## PTZ Control

When `ptz_ip` is configured, the backend drives a PTZ camera's Hikvision ISAPI continuous-move endpoint (rotation and zoom) plus the FocusData focus endpoint on behalf of all connected UIs. `ptz_username` and `ptz_password` are optional and are tried as digest auth first, falling back to basic auth on a `401` response. Setting `ptz_ip` to an empty string disables PTZ and stops all camera HTTP traffic.

The UI sends held PTZ requests over the controls WebSocket; `direction`, `zoom`, and `focus` are independent channels:

```json
{"type":"ptz","direction":"left","zoom":null,"focus":null}
{"type":"ptz","direction":null,"zoom":"zoom-in","focus":null}
{"type":"ptz","direction":null,"zoom":null,"focus":"focus-near"}
```

- `direction`: `"left"`, `"right"`, `"up"`, `"down"`, or `null`.
- `zoom`: `"zoom-in"`, `"zoom-out"`, or `null`.
- `focus`: `"focus-near"`, `"focus-far"`, or `null`.

A background loop sends exactly one rotation/zoom ISAPI command per tick (rotation takes priority over zoom, stop is sent when neither is active). Every rotation/zoom command is re-sent at 5 Hz — including stop, which is re-sent continuously while no request is active so the camera always halts even if the UI disconnects, crashes, or a stop packet is lost. Camera movements map to ISAPI pan/tilt values and zoom to the ISAPI zoom channel; both use fixed speeds.

Focus is edge-triggered instead of deadman-repeated: one `FocusData` command goes to `PUT /ISAPI/System/Video/inputs/channels/<n>/focus` when a focus value first appears, and one zero-speed `FocusData` stop is sent when it clears (including when the last UI disconnects). Rotation and zoom stop packets on the `PTZData` channel are unaffected.

## Binary UDP Schema

`../packets-schema.json` is the source of truth for the command header, endian, ordered fields, defaults, numeric types, and bounds. Supported field types are `int8`, `uint8`, `int16`, `uint16`, `int32`, `uint32`, `float32`, and `float64`.

The shipped command is exactly 11 bytes:

| Offset | Size | Encoding                | Value    |
| ------ | ---- | ----------------------- | -------- |
| `0`    | 3    | ASCII                   | `ITS`    |
| `3`    | 4    | little-endian `float32` | `vy`     |
| `7`    | 4    | little-endian `float32` | `vtheta` |

The telemetry receiver binds `0.0.0.0:8889` by default. Its packet is exactly 4 bytes: ASCII `ITS` followed by `battery_level` as a `uint8` percentage constrained to `0..100`. Packets with a wrong header, wrong length, or out-of-range value are discarded.

Adding a Vue input requires adding its initial value to `useControlState.js`, binding the component through `updatePacket()`, and adding the corresponding ordered field to `packets-schema.json`. The WebSocket dispatcher and UDP encoder require no field-specific handler or offset.

## Run

Requirements are Python 3.10+, FastAPI, Uvicorn, and OpenCV Python. Run one worker from `backend/` because runtime settings, connected-client count, packet state, and camera capture are process-local:

```bash
uvicorn server:app --host 127.0.0.1 --port 8000
```

The frontend defaults to `http://127.0.0.1:8000`. It can be built with a different local endpoint through `VITE_BACKEND_URL`, with the corresponding CSP allowlist updated in `frontend/index.html`.

## Docker

The project image bundles the backend with the frontend. Inside the container the backend is started from `/app/backend` and `PYTHONPATH=/app/backend` is set so modules resolve regardless of working directory. The Docker entrypoint waits for `/health` before launching Electron.

## Behavior

UDP transmission runs only while at least one controls WebSocket is connected and a complete destination is enabled. Disconnecting the final UI resets all controls to schema defaults. The app sends no special final stop datagram; the robot must enforce a UDP receive-timeout watchdog.

`camera_backend` defaults to `go2rtc`; `aiortc` remains available as an explicit alternative. With go2rtc, FastAPI starts a localhost-only go2rtc process on demand, configures the named `robot-camera` stream, and proxies `/offer` SDP to go2rtc. The go2rtc API listens on `127.0.0.1:1984` and WebRTC media uses port `8555`. Set `GO2RTC_BINARY` to override the executable path during local development. A selected but unavailable go2rtc binary reports a camera error and does not silently fall back to aiortc.

With aiortc, each `/offer` creates an aiortc peer and RTSP media player. Camera backends are closed when the camera URL/backend changes or FastAPI shuts down. The current deployment assumes the renderer and backend share the Steam Deck host; no STUN/TURN service is configured.

## Validation

Run the focused codec tests from `backend/`:

```bash
python -m unittest test_utils
```

To run the same tests inside the built container:

```bash
docker run --rm --network host -v "$PWD/..:/app" -w /app/backend \
	steamdeck-robot-monitor:latest python -m unittest test_utils
```

`../scripts/udp_server_simulation.py` decodes received commands. `../scripts/udp_telemetry_simulation.py 75` sends one schema-derived battery packet to the default telemetry port. Static validation does not require a camera or live UDP target.

## Limitations

- Runtime state is shared by all connected UIs and requires one Uvicorn worker.
- Telemetry currently contains only battery percentage; acknowledgement, sequence IDs, RTT, and loss are not implemented.
- WebRTC requires a reachable RTSP source. go2rtc is bundled in Docker and may use FFmpeg for codec conversion; local development requires `GO2RTC_BINARY` or a `go2rtc` executable on `PATH`.
- The current WebRTC ICE configuration is intended for local host/container playback only.
- RTSP credentials are supplied in `camera_url` userinfo and should not be written to logs.
