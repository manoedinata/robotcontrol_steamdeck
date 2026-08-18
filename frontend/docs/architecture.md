# Architecture

## Process Boundaries

### Local development

```text
Vue renderer                    Electron main                 FastAPI backend
-------------                   -------------                 ---------------
Camera/control UI               BrowserWindow                WS /ws/controls
Gamepad and touch input  <IPC>  Settings JSON                Binary UDP at 50 Hz
Typed WebSocket client          Exit/lifecycle               POST /offer (WebRTC)
Battery telemetry HUD                                       UDP telemetry receiver
```

### Docker / Steam deployment

```text
Host Steam
   |
   +-- launch-from-steam.sh (Docker/Podman)
           |
           +-- container
                   +-- docker-entrypoint.sh
                   |       +-- uvicorn backend on 127.0.0.1:8000
                   |       +-- wait for /health
                   |       +-- Electron frontend on same host
                   |
                   +-- /app/config bind-mounted from host
```

Electron has no robot or camera transport code. It exposes only `quitApp()`, `loadSettings()`, and `saveSettings(settings)` through a context-isolated preload. `nodeIntegration` remains disabled.

Vue owns input interpretation and UI state. `useBackendConnection.js` owns one WebSocket, reconnects every two seconds, replays latest configuration and control state after connection, and tracks live/stale telemetry. `useControlState.js` owns the packet object and coalesces reactive updates per animation frame. `useSettings.js` persists the frontend settings shape, including the telemetry listening port, keeps RTSP credentials separate from the source URL, and translates them into backend configuration.

FastAPI owns network configuration, schema-driven packet validation/encoding/decoding, the 50 Hz command task, the independently bound telemetry receiver, RTSP media players, and WebRTC signaling. `packets-schema.json` at the repository root is the binary packet source of truth.

The Docker image uses host networking so the frontend renderer continues to connect to `http://127.0.0.1:8000` without cross-container DNS. The entrypoint starts both processes, waits for backend readiness via `GET /health`, and shuts them down together when Electron exits. Settings are persisted in a bind-mounted host directory controlled by `APP_SETTINGS_DIR`.

The camera and controller stay mounted when the Settings drawer opens, so transport and control state remain active. Backend disconnection is shown in the Home HUD and does not block local UI operation.

## Repository Map

```text
frontend/
  main.js                         Electron shell/settings
  electron-components/preload.js Restricted renderer bridge
  src/composables/
    useBackendConnection.js       WebSocket, telemetry state, WebRTC signaling
    useControlState.js            Generic packet state
    useSettings.js                Persisted settings/config sync
  src/components/                 Camera, controls, Settings shell, keyboard
  src/views/                      Home and Settings content
backend/
  server.py                       WebSocket, UDP send/receive, WebRTC signaling
  utils.py                        Binary schema encoder/decoder
scripts/
  udp_server_simulation.py        Bidirectional command/telemetry simulator
  udp_telemetry_simulation.py     One-shot battery telemetry sender
packets-schema.json               Ordered command and telemetry layouts
```

The backend is a separately launched local service. Electron does not spawn, restart, or terminate it.
