# Steam Deck Robot Monitor Backend

FastAPI owns robot and camera transport for the Steam Deck UI. Vue sends configuration and control state over a local WebSocket; the backend sends the latest command as a binary UDP datagram at 50 Hz and exposes the configured camera as HTTP MJPEG.

## Endpoints

- `WS /ws/controls`: typed configuration and control messages.
- `GET /stream`: `multipart/x-mixed-replace` MJPEG for an HTML `<img>`.

Configuration message (RTSP credentials may be supplied as URL-encoded userinfo):

```json
{"type":"config","config":{"udp_host":"127.0.0.1","udp_port":8888,"camera_url":"rtsp://user:password@camera/stream"}}
```

Control messages may update any subset of schema fields:

```json
{"type":"control","packet":{"vy":1.5,"vtheta":-0.25}}
```

Invalid messages receive `{"type":"error","message":"..."}` without closing the connection. Empty `udp_host` plus port `0` disables UDP. Empty `camera_url` leaves camera capture idle.

## Binary UDP Schema

`../packets-schema.json` is the source of truth for the command header, endian, ordered fields, defaults, numeric types, and bounds. Supported field types are `int8`, `uint8`, `int16`, `uint16`, `int32`, `uint32`, `float32`, and `float64`.

The shipped command is exactly 11 bytes:

| Offset | Size | Encoding                | Value    |
| ------ | ---- | ----------------------- | -------- |
| `0`    | 3    | ASCII                   | `ITS`    |
| `3`    | 4    | little-endian `float32` | `vy`     |
| `7`    | 4    | little-endian `float32` | `vtheta` |

Adding a Vue input requires adding its initial value to `useControlState.js`, binding the component through `updatePacket()`, and adding the corresponding ordered field to `packets-schema.json`. The WebSocket dispatcher and UDP encoder require no field-specific handler or offset.

## Run

Requirements are Python 3.10+, FastAPI, Uvicorn, and OpenCV Python. Run one worker from `backend/` because runtime settings, connected-client count, packet state, and camera capture are process-local:

```bash
uvicorn server:app --host 127.0.0.1 --port 8000
```

The frontend defaults to `http://127.0.0.1:8000`. It can be built with a different local endpoint through `VITE_BACKEND_URL`, with the corresponding CSP allowlist updated in `frontend/index.html`.

## Behavior

UDP transmission runs only while at least one controls WebSocket is connected and a complete destination is enabled. Disconnecting the final UI resets all controls to schema defaults. The app sends no special final stop datagram; the robot must enforce a UDP receive-timeout watchdog.

Camera capture starts on demand for the first `/stream` subscriber. One background OpenCV worker captures and JPEG-encodes frames for all subscribers, reconnects after failures, responds to runtime URL changes, and stops after the final viewer disconnects.

## Validation

Run the focused codec tests from `backend/`:

```bash
python -m unittest test_utils
```

`../scripts/udp_server_simulation.py` decodes received commands from the same schema for local diagnostics. Static validation does not require a camera or live UDP target.

## Limitations

- Runtime state is shared by all connected UIs and requires one Uvicorn worker.
- UDP is send-only in the current implementation; robot telemetry and acknowledgement are not exposed.
- MJPEG re-encoding uses CPU and more bandwidth than forwarding compressed H.264/H.265.
- OpenCV support and `CAP_PROP_BUFFERSIZE` behavior vary by platform.
- RTSP credentials are supplied in `camera_url` userinfo and should not be written to logs.
