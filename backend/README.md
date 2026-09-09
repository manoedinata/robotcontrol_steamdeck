# Steam Deck Robot Monitor Backend

FastAPI owns robot transport and all camera transport for the Steam Deck UI. Vue sends configuration and control state over a local WebSocket; the backend sends commands at 50 Hz, receives battery telemetry on a separate UDP port, and converts every camera source to local WebRTC through the selected camera backend. Every configured source is kept warm at once so the UI can switch sources without a reconnect.

Two source kinds share one path. An RTSP url is dialed by the camera backend directly. A direct camera WebSocket url (`ws://`/`wss://`) is opened by `CameraWebSocketSource.py`, which sends the camera's `PlayStream2` handshake, holds exactly one connection per camera, and re-serves the bytestream at `GET /camera/<id>/stream` so go2rtc or aiortc can consume it like any other input. The renderer never connects to a camera.

## Endpoints

- `WS /ws/controls`: typed configuration and control messages.
- `POST /offer?src=<stream id>`: WebRTC SDP signaling for a receive-only video peer, for every source kind. `src` selects which configured stream the answer is for; it may be omitted only when exactly one stream is configured.
- `GET /camera/<stream id>/stream`: the raw bytestream of one direct camera WebSocket source. This is an internal seam between the WebSocket hub and the camera backend, not a renderer endpoint.
- `GET /health`: readiness probe used by the Docker entrypoint and external health checks.

Configuration message (RTSP credentials may be supplied as URL-encoded userinfo). `camera_streams` is the list of camera sources to keep connected, each with a renderer-assigned `id`:

```json
{"type":"config","config":{"udp_host":"127.0.0.1","udp_port":8888,"udp_listen_port":8889,"camera_streams":[{"id":"cam-0","url":"rtsp://user:password@camera/stream"}],"camera_backend":"go2rtc"}}
```

Control messages may update any subset of schema fields:

```json
{"type":"send","packet":{"vy":1.5,"vtheta":-0.25}}
```

Invalid messages receive `{"type":"error","message":"..."}` without closing the connection. Empty `udp_host` plus port `0` disables UDP. An empty or absent `camera_streams` list leaves camera capture idle. Stream ids must match `[A-Za-z0-9_-]{1,64}` and be unique; each url must be a valid `rtsp://`, `ws://`, or `wss://` URL with a hostname.

Valid robot telemetry is broadcast to all connected UIs:

```json
{"type":"receive","packet":{"battery_level":75}}
```

## Recording

One button records every configured source at once, one ffmpeg per source,
stream-copied (`-c copy`) into Matroska. Nothing is re-encoded.

```json
{"type":"record","action":"start"}
{"type":"record","action":"stop"}
```

Recording state is broadcast on every change, every two seconds while active,
and once to each UI on connect. It never contains a camera URL:

```json
{"type":"recording","active":true,"session_id":"20260909-123015",
 "directory":"/app/recordings/20260909-123015","free_bytes":43257610240,
 "seconds_remaining":2540,"stopped_reason":null,
 "sources":[{"id":"cam-0","kind":"rtsp","status":"recording","part":1,
             "file":"cam-0_001.mkv","bytes":18234312,"restarts":0,"error":null}]}
```

Per-source `status` is `idle`, `starting`, `recording`, `reconnecting`,
`failed`, or `stopped`; `stopped_reason` is `null`, `operator`, `low_disk`, or
`shutdown`. One source failing never stops the others.

Files go to `$RECORDINGS_DIR/<YYYYmmdd-HHMMSS>/<stream id>_<part>.mkv`
(`/app/recordings` in the container, bind-mounted from
`${SDRM_RECORDINGS_DIR:-$HOME/Videos/steamdeck-robot-monitor}`). The path is an
environment variable, never a config field.

Behavior worth knowing:

- The source list is frozen when recording starts. Switching cameras in the UI
  re-sends the whole config, so reacting to it would split every recording into
  parts each time the operator pressed B.
- A source that has written video is retried for the rest of the session with
  capped backoff, so a camera that reboots or drives out of range comes back on
  a new part. A source that never wrote a single byte is treated as
  misconfigured and gives up after three quick failures.
- A recording that stops growing for 15 s is restarted: ffmpeg will otherwise
  sit forever on an RTSP session that went quiet without erroring.
- Recording continues across a UI reconnect. Only an explicit stop, low disk, or
  backend shutdown ends it.
- It refuses to start below 2 GiB free and stops cleanly below 512 MiB, which
  trailers every file rather than letting several hit `ENOSPC` at once.

## PTZ Control

When `ptz_ip` is configured, the backend drives a PTZ camera's Hikvision ISAPI continuous-move endpoint (rotation and zoom) plus the FocusData focus endpoint on behalf of all connected UIs. The camera credentials are hardcoded (`PTZ_USERNAME`/`PTZ_PASSWORD` in `PTZController.py`) and are tried as digest auth first, falling back to basic auth on a `401` response. Setting `ptz_ip` to an empty string disables PTZ and stops all camera HTTP traffic.

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

`camera_backend` defaults to `go2rtc`; `aiortc` remains available as an explicit alternative. With go2rtc, FastAPI starts one localhost-only go2rtc process on demand and registers every configured source as a named stream (the renderer's stream id), then proxies `/offer?src=<id>` SDP to go2rtc. A relayed WebSocket source is registered as an `exec:` ffmpeg source rather than a plain url, because the relay serves a bare bytestream with no container to identify it and the demuxer has to be named; the backend sniffs the camera's first payload to pick `h264` or `mjpeg`. A renderer that holds one peer per stream keeps every source connected, so switching is instant. The go2rtc API listens on `127.0.0.1:1984` and WebRTC media uses port `8555`. Set `GO2RTC_BINARY` to override the executable path during local development. A selected but unavailable go2rtc binary reports a camera error and does not silently fall back to aiortc.

With aiortc, each `/offer` creates an aiortc peer and RTSP media player for the requested stream. A config change closes only the peers whose source id or url actually changed; removing a stream or switching backend closes its peers, and FastAPI shutdown closes everything. The current deployment assumes the renderer and backend share the Steam Deck host; no STUN/TURN service is configured.

## Validation

Run the tests from `backend/`:

```bash
python -m unittest discover -s . -p "test_*.py"
```

To run the same tests inside the built container:

```bash
docker run --rm --network host -v "$PWD/..:/app" -w /app/backend \
	steamdeck-robot-monitor:latest python -m unittest discover -s . -p "test_*.py"
```

`../scripts/udp_server_simulation.py` decodes received commands. `../scripts/udp_telemetry_simulation.py 75` sends one schema-derived battery packet to the default telemetry port. Static validation does not require a camera or live UDP target.

## Limitations

- Runtime state is shared by all connected UIs and requires one Uvicorn worker.
- Telemetry currently contains only battery percentage; acknowledgement, sequence IDs, RTT, and loss are not implemented.
- WebRTC requires a reachable RTSP source. go2rtc is bundled in Docker and may use FFmpeg for codec conversion; local development requires `GO2RTC_BINARY` or a `go2rtc` executable on `PATH`.
- The current WebRTC ICE configuration is intended for local host/container playback only.
- RTSP credentials are supplied in each `camera_streams[].url` userinfo and should not be written to logs.
- A recording adds one RTSP connection per recorded RTSP source, on top of the one the live view already holds. WebSocket sources cost nothing extra: the recorder is another subscriber to the hub's single connection.
- A backend killed with `SIGKILL` leaves its ffmpeg children running. They stop themselves at the per-file time limit, and container teardown reaps them in the Docker deployment. A recording interrupted that way is missing its Matroska trailer but still plays; `ffmpeg -i in.mkv -c copy out.mkv` rebuilds the index.
- A direct camera WebSocket source adds one relay hop (camera socket to local HTTP to the camera backend), which costs latency the old renderer-direct WebCodecs path did not. Nothing is re-encoded on that hop.
- The relay address assumes the backend is reachable at `127.0.0.1:8000`; set `APP_BACKEND_PORT` when uvicorn runs on another port.
- Every configured RTSP source stays connected while a UI is open, so CPU, GPU, and bandwidth cost scales with the number of sources.
