# Steam Deck Robot Monitor

All-in-one RTSP camera and control UI for a differential-drive robot, designed for the Steam Deck.

The application consists of a FastAPI backend that handles camera capture, binary UDP command encoding, a 50 Hz command sender, battery telemetry reception, and host ping measurements, plus an Electron + Vue 3 frontend that provides the gamepad/touch/keyboard UI and telemetry HUD.

## Repository Layout

```text
backend/          FastAPI server and packet utilities
frontend/         Electron/Vite/Vue UI
scripts/          Local command receiver and telemetry sender simulations
packaging/        Docker build and Steam launcher scripts
packets-schema.json   Binary UDP command and telemetry schemas
Dockerfile        Multi-stage image that bundles FE + BE
```

## Quick Start (Local Development)

Start the backend from `backend/`:

```bash
uvicorn server:app --host 127.0.0.1 --port 8000
```

Then start the frontend from `frontend/`:

```bash
npm install
npm run dev
```

See [backend/README.md](backend/README.md) and [frontend/README.md](frontend/README.md) for details.

## Quick Start (Docker / Steam)

A single container bundles both frontend and backend, so the app can be launched from Steam's non-game library.

### 1. Build the image

```bash
./packaging/build-image.sh
```

### 2. Launch from Steam

Add `./packaging/launch-from-steam.sh` as a non-Steam game. The script:

- Detects Docker or Podman
- Uses host networking so the backend stays on `127.0.0.1:8000`
- Passes through Wayland/X11 and input devices for SteamOS
- Persists settings in `~/.config/steamdeck-robot-monitor` (override with `SDRM_CONFIG_DIR`)

```bash
./packaging/launch-from-steam.sh
```

Environment overrides for the launcher:

| Variable          | Default                             | Meaning                 |
| ----------------- | ----------------------------------- | ----------------------- |
| `SDRM_IMAGE`      | `steamdeck-robot-monitor:latest`    | Image name/tag          |
| `SDRM_APP_NAME`   | `steamdeck-robot-monitor`           | Running container name  |
| `SDRM_CONFIG_DIR` | `~/.config/steamdeck-robot-monitor` | Host settings directory |

### Container Runtime

Inside the image, `/usr/local/bin/docker-entrypoint.sh`:

1. Starts `uvicorn` in `/app/backend` on `127.0.0.1:8000`
2. Polls `/health` until the backend is ready
3. Starts Electron (`/app/frontend/main.js`) with the production renderer bundle
4. Shuts down both processes together when Electron exits

Settings are written under `/app/config`, which the launcher bind-mounts from the host config directory. Set `APP_SETTINGS_DIR` inside the container to change the settings path.

## Documentation

- [Backend README](backend/README.md)
- [Frontend README](frontend/README.md)
- [Frontend setup and launch guide](frontend/docs/setup.md)
- [Architecture and security](frontend/docs/architecture.md)
- [Controls](frontend/docs/controls.md)
- [Camera and settings](frontend/docs/camera-and-settings.md)
- [UDP and WebSocket contract](frontend/docs/udp.md)
- [Limitations](frontend/docs/limitations.md)

## Validation

Backend codec tests:

```bash
cd backend
python -m unittest test_utils
```

Docker image build:

```bash
./packaging/build-image.sh
```

Run backend tests inside the built container:

```bash
docker run --rm --network host -v "$PWD:/app" -w /app/backend \
	steamdeck-robot-monitor:latest python -m unittest test_utils
```

Interactive runtime validation should be performed by the user on the target device. Camera playback requires a reachable RTSP source; FastAPI converts it to local WebRTC for the Electron renderer.
