# Steam Deck Robot Monitor Frontend

A Steam Deck-oriented Electron and Vue UI for viewing a camera stream and controlling a differential-drive robot. RTSP uses backend WebRTC; the camera WebSocket mode connects directly from the renderer and decodes H.264 with WebCodecs. Electron remains the desktop shell and settings store. Python/FastAPI owns RTSP transport, binary UDP command transmission, and robot telemetry reception.

## Features

- Camera-first frameless UI for the Steam Deck's 1280x800 viewport
- RTSP camera sources through backend WebRTC signaling, with optional credentials and selectable go2rtc/aiortc backend
- Multiple configurable camera sources, all kept connected at once and cycled instantly with B (Circle)
- Pointer, touch, Steam Deck, and compatible gamepad controls
- Configurable linear Y and angular theta limits
- PTZ camera control (pan/tilt via D-pad at a speed set by the Home slider, zoom via shoulder buttons, focus on X or the on-screen buttons, the infrared light on Y or its own button) using an optional ISAPI camera address; the camera credentials are hardcoded in the backend
- Recordings library: a scrollable list of past sessions with time, length, size, and which cameras actually produced video, expandable into per-file playback, save, and delete
- Forward/reverse drive-direction toggle in the shell stack; the left stick travels up only and the mode decides the sign
- Settings drawer and built-in gamepad-navigable keyboard
- Automatic backend WebSocket reconnect and current-state replay
- Host ping latency to the configured UDP destination in the Home HUD
- Distance travelled in the Home HUD, from differential-drive odometry over the robot's two reported wheel speeds; the battery readout there shows the Steam Deck's own battery, since this robot reports no battery of its own
- Persistent camera sources, RTSP credentials, camera backend, RTSP transport, UDP command destination, telemetry listening port, velocity, keyboard, and PTZ camera settings

## Quick Start

### Local development

Start the backend separately from `../backend`:

```bash
uvicorn server:app --host 127.0.0.1 --port 8000
```

Then install and run the UI:

```bash
npm install
npm run dev
```

`npm run dev` starts Vite and Electron. `npm run start` builds and launches the production UI. `./launch.sh` launches an existing build for Steam Gaming Mode.

The renderer defaults to `http://127.0.0.1:8000`. Set `VITE_BACKEND_URL` at build/dev time to use another local backend URL, and keep the Content Security Policy in `index.html` aligned.

### Docker / Steam

For a single container that launches both frontend and backend from Steam, see the [packaging scripts](../packaging/). The container entrypoint starts FastAPI, waits for `/health`, then launches Electron. Settings are persisted in a bind-mounted host directory (`~/.config/steamdeck-robot-monitor` by default).

```bash
../packaging/build-image.sh
../packaging/launch-from-steam.sh
```

## Data Flow

`useBackendConnection.js` owns the singleton WebSocket and reconnect lifecycle. It sends typed messages:

```json
{"type":"config","config":{"udp_host":"127.0.0.1","udp_port":8888,"udp_listen_port":8889,"camera_streams":[{"id":"cam-0","url":"rtsp://camera/stream"}],"camera_backend":"go2rtc","rtsp_transport":"tcp"}}
```

```json
{"type":"send","packet":{"vy":0,"vtheta":0}}
```

`useControlState.js` owns the generic reactive packet object and coalesces changes to one publication per animation frame. Every configured source has its own always-connected `CameraFeed.vue`; the Home view mounts them all and only shows the active one, so switching sources never reconnects. An RTSP feed negotiates receive-only WebRTC through FastAPI `/offer?src=<id>` and never contacts go2rtc or the camera directly; a WebSocket feed connects straight to the camera.

The backend broadcasts received telemetry as `{"type":"receive","packet":{"position_left":1234.5,"position_right":1230.1,"speed_left":48.0,"speed_right":47.8}}`. The renderer validates each field it knows, and marks the values stale after two seconds without another packet.

The backend broadcasts `{"type":"camera","streams":["cam-0"]}` when it has
re-dialed a source, because its url, its credentials, `camera_backend` or
`rtsp_transport` changed. The named feeds reconnect at once and show
"Connecting to camera..." while they do. The renderer deliberately does not
reconnect when the settings form is saved: the backend is what owns camera
transport, and it says so only once the change is actually in force.

Each feed also counts the video frames its peer receives. Five seconds without
one is a dead feed however healthy the connection claims to be -- an unplugged
camera usually leaves its connection open and simply goes quiet -- so the feed
drops into the same error state as any other WebRTC failure: a "Hubungkan
lagi" button plus a background retry every two seconds, either of which
re-offers with `?restart=1`, the backend's cue to throw away the connection it
holds for that source and dial the camera afresh. A feed counts as connected
only once frames actually arrive, so a camera that answers and then sends
nothing never shows as a black rectangle or as the last frame of the source
before it.

The backend also broadcasts host reachability as `{"type":"ping","ping_ms":12.4}` (or `null` when disabled/unreachable); the Home HUD displays it as `Ping`. This is not exact command-datagram RTT because the current robot protocol has no acknowledgement or sequence ID.

## Documentation

- [Setup and launch](docs/setup.md)
- [Camera and Settings](docs/camera-and-settings.md)
- [Controls](docs/controls.md)
- [UDP and WebSocket contract](docs/udp.md) — includes the step-by-step guide for adding a new UDP packet field
- [Architecture and security](docs/architecture.md)
- [Current limitations](docs/limitations.md)
- [Contributor guidance](AGENTS.md)

## Stack and Validation

Electron, Vue 3, Vite, Bootstrap 5, Sass, and Lucide icons. Node.js `22+` is required. Run `npm run build` after frontend changes. There is no frontend lint or automated test script.
