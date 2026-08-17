# Setup and Launch

## Requirements

- Node.js `22+` and npm
- Python 3.10+, FastAPI, Uvicorn, and OpenCV Python
- A reachable HTTP, HTTPS, or RTSP camera when camera display is needed
- A UDP robot endpoint when command transmission is needed
- Docker or Podman (for the bundled Steam container path)

## Backend

The backend is a separate process. From `backend/`:

```bash
uvicorn server:app --host 127.0.0.1 --port 8000
```

Use one worker. Runtime config, current packet, connected UI count, and camera capture are process-local.

## Frontend Development

From `frontend/`:

```bash
npm install
npm run dev
```

Vite listens on `127.0.0.1:5173` and Electron opens it. The renderer connects to `http://127.0.0.1:8000` by default. To use another local base URL, set `VITE_BACKEND_URL` and update the CSP in `index.html` to permit its WebSocket and image endpoints.

| Command            | Purpose                                |
| ------------------ | -------------------------------------- |
| `npm run dev`      | Run Vite and Electron                  |
| `npm run build`    | Build renderer into `dist/`            |
| `npm run electron` | Launch Electron with existing `dist/`  |
| `npm run start`    | Build and launch Electron              |
| `npm run preview`  | Browser preview of production renderer |

The browser preview cannot persist settings or invoke Exit because those require the preload bridge.

## Steam Gaming Mode (Container)

The recommended way to launch from Steam is the bundled Docker image:

```bash
../packaging/build-image.sh
../packaging/launch-from-steam.sh
```

`launch-from-steam.sh` can be added directly to Steam as a non-Steam game. It:

- Detects Docker or Podman
- Uses host networking so the backend remains on `127.0.0.1:8000`
- Mounts the host graphics/display sockets and input devices
- Persists settings in `~/.config/steamdeck-robot-monitor`

Environment overrides:

| Variable          | Default                             | Meaning                 |
| ----------------- | ----------------------------------- | ----------------------- |
| `SDRM_IMAGE`      | `steamdeck-robot-monitor:latest`    | Image name/tag          |
| `SDRM_APP_NAME`   | `steamdeck-robot-monitor`           | Running container name  |
| `SDRM_CONFIG_DIR` | `~/.config/steamdeck-robot-monitor` | Host settings directory |

### Local Steam Gaming Mode (without Docker)

After `npm install` and `npm run build`, run `./launch.sh`. It locates Electron's native binary and existing `dist/index.html`, clears `LD_PRELOAD`, and launches the UI. It does not start or supervise FastAPI; arrange backend startup separately.

## Diagnostics

Run `python scripts/udp_server_simulation.py` from the repository root to receive and decode schema-defined packets on `127.0.0.1:8888`. Change its constants when testing another local port. The script does not emulate robot telemetry or a motion watchdog.
