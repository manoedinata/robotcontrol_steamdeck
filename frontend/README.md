# Steam Deck Robot Monitor Frontend

A Steam Deck-oriented Electron and Vue UI for viewing the backend MJPEG camera stream and controlling a differential-drive robot. Electron is only the desktop shell and settings store. Python/FastAPI owns camera capture, binary UDP encoding, and 50 Hz UDP transmission.

## Features

- Camera-first frameless UI for the Steam Deck's 1280x800 viewport
- HTTP, HTTPS, and RTSP camera sources through backend `GET /stream`
- Pointer, touch, Steam Deck, and compatible gamepad controls
- Configurable linear Y and angular theta limits
- Settings drawer and built-in gamepad-navigable keyboard
- Automatic backend WebSocket reconnect and current-state replay
- Persistent camera, UDP destination, velocity, and keyboard settings

## Quick Start

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

## Data Flow

`useBackendConnection.js` owns the singleton WebSocket and reconnect lifecycle. It sends typed messages:

```json
{"type":"config","config":{"udp_host":"127.0.0.1","udp_port":8888,"camera_url":"rtsp://camera/stream"}}
```

```json
{"type":"control","packet":{"vy":0,"vtheta":0}}
```

`useControlState.js` owns the generic reactive packet object and coalesces changes to one publication per animation frame. Camera rendering points an HTML `<img>` directly at backend `/stream`.

## Documentation

- [Setup and launch](docs/setup.md)
- [Camera and Settings](docs/camera-and-settings.md)
- [Controls](docs/controls.md)
- [UDP and WebSocket contract](docs/udp.md)
- [Architecture and security](docs/architecture.md)
- [Current limitations](docs/limitations.md)
- [Contributor guidance](AGENTS.md)

## Stack and Validation

Electron, Vue 3, Vite, Bootstrap 5, Sass, and Lucide icons. Node.js `20.19+` or `22.12+` is required by Vite 8. Run `npm run build` after frontend changes. There is no frontend lint or automated test script.
