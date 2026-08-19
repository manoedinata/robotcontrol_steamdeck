# Steam Deck Robot Monitor Frontend

A Steam Deck-oriented Electron and Vue UI for viewing the backend WebRTC camera stream and controlling a differential-drive robot. Electron is only the desktop shell and settings store. Python/FastAPI owns RTSP camera transport, binary UDP command transmission, and robot telemetry reception.

## Features

- Camera-first frameless UI for the Steam Deck's 1280x800 viewport
- RTSP camera source through backend WebRTC signaling, with optional credentials and selectable go2rtc/aiortc backend
- Pointer, touch, Steam Deck, and compatible gamepad controls
- Configurable linear Y and angular theta limits
- Settings drawer and built-in gamepad-navigable keyboard
- Automatic backend WebSocket reconnect and current-state replay
- Host ping latency to the configured UDP destination in the Home HUD
- Live/stale robot battery percentage in the Home HUD
- Persistent camera source, RTSP credentials, camera backend, UDP command destination, telemetry listening port, velocity, and keyboard settings

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
{"type":"config","config":{"udp_host":"127.0.0.1","udp_port":8888,"udp_listen_port":8889,"camera_url":"rtsp://camera/stream","camera_backend":"go2rtc"}}
```

```json
{"type":"send","packet":{"vy":0,"vtheta":0}}
```

`useControlState.js` owns the generic reactive packet object and coalesces changes to one publication per animation frame. `CameraFeed.vue` negotiates receive-only WebRTC through FastAPI `/offer`; it does not contact go2rtc or the camera directly.

The backend broadcasts received telemetry as `{"type":"receive","packet":{"battery_level":75}}`. The renderer validates the percentage and marks the value stale after two seconds without another packet.

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
