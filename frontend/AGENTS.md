# Frontend Guidance

## Purpose

This directory is the Steam Deck UI. Electron provides the desktop window, application lifecycle, atomic settings persistence, and Exit action. It must not own UDP sockets, camera relays, transcoding, packet encoding, or backend process supervision.

## Architecture

- `main.js`: BrowserWindow, application lifecycle, settings load/save IPC, and Exit IPC.
- `electron-components/preload.js`: narrow `quitApp`, `loadSettings`, and `saveSettings` bridge.
- `src/App.vue`: persistent command shell, backend connection lifecycle, and Settings drawer state.
- `src/views/HomeView.vue`: camera, backend/camera status, controller status, and control composition.
- `src/views/SettingsView.vue`: camera source, UDP destination, velocity limits, and keyboard settings.
- `src/components/CameraFeed.vue`: backend MJPEG `<img>` and reconnect state.
- `src/components/ControllerPanel.vue`: Y/theta input mapping and generic packet updates.
- `src/composables/useBackendConnection.js`: singleton typed WebSocket transport, telemetry freshness, reconnect, replay, and backend stream URL.
- `src/composables/useControlState.js`: generic reactive command packet and frame-coalesced publication.
- `src/composables/useSettings.js`: shared persisted settings and backend config synchronization.
- `../packets-schema.json`: backend-owned binary UDP command layout.

## Required Invariants

### Electron

Keep `contextIsolation: true` and `nodeIntegration: false`. Do not expose generic IPC, filesystem, shell, network, process-spawn, or packet APIs. Keep settings writes as temporary-file plus rename.

In local development the backend is launched separately and is not supervised by Electron. In the Docker/Steam path, both processes run inside the same container and are supervised by `docker-entrypoint.sh`; Electron still must not spawn the backend itself.

Settings directory is configurable through `APP_SETTINGS_DIR` at runtime. Default to a path under the user's config home when the variable is absent, and use the provided path verbatim when present. This is required for Docker bind-mounts.

### Backend Connection

The default backend base URL is `http://127.0.0.1:8000`; `VITE_BACKEND_URL` may replace it at build/dev time. Keep `index.html` CSP aligned with allowed local WebSocket and stream endpoints.

WebSocket messages are separated by `type`:

- `{ "type": "config", "config": { "udp_host", "udp_port", "udp_listen_port", "camera_url" } }`
- `{ "type": "control", "packet": { ...schemaFields } }`
- Backend telemetry uses `{ "type": "telemetry", "packet": { "battery_level": 0..100 } }`.
- Backend errors use `{ "type": "error", "message": "..." }`.

Reconnect automatically and replay latest config before latest control state. Components must not create their own sockets.

### Extending Controls

To add a command input, initialize its field in `useControlState.js`, bind the component through `updatePacket({ field: value })`, and add the ordered binary field to `../packets-schema.json`. Do not add field-specific transport handlers, IPC methods, or binary offsets. See [docs/udp.md#extending-the-udp-packet](docs/udp.md#extending-the-udp-packet) for the concrete three-step recipe.

Current control mapping remains:

- Left stick vertical axis controls `vy`; up is positive.
- Right stick horizontal axis controls `vtheta`; right is positive.
- Gamepad dead zone is `0.12`; pointer/touch has no dead zone.
- Velocity is scaled by `maxYVelocity` and `maxThetaVelocity`, default `10`, bounded `0.1..100` in Settings.

### Settings

Preserve this persisted contract:

```json
{
  "cameraUrl": "http://192.168.1.20:8080/video",
  "cameraUsername": "",
  "cameraPassword": "",
  "maxYVelocity": 10,
  "maxThetaVelocity": 10,
  "udpHost": "192.168.1.30",
  "udpPort": 5000,
  "udpListenPort": 8889,
  "useOnScreenKeyboard": true
}
```

Empty UDP host and port `0` disable transmission. The camera form supports HTTP and RTSP selections, preserves RTSP credentials in separate persisted fields, and does not preserve query/fragment data. Credentials are included only in the transient authenticated `camera_url` sent to the backend. Keep `useSettings.js` as the renderer source of truth.

### Camera

`CameraFeed.vue` must consume backend `/stream`; it never receives the configured source directly. Empty camera settings show idle. Image errors retry every two seconds. Do not restore Electron camera relays or bundled ffmpeg.

### UI and Navigation

Keep the camera-first operational surface mounted while Settings opens as a drawer. Preserve the 1280x800 target, 960x640 minimum, existing Bootstrap/Lucide/local Sass design, joystick dimensions, shell action focus, on-screen keyboard behavior, and Gamepad API ownership in `useGamepad.js`.

## Commands

- `npm run dev`: Vite plus Electron; backend is separate.
- `npm run build`: production renderer build and minimum validation.
- `npm run electron`: launch existing `dist`.
- `npm run start`: build then launch.
- `npm run preview`: browser preview; settings/Exit require Electron.

Vite 8 requires Node.js `20.19+` or `22.12+`. There is no frontend test or lint script. Do not run a server or browser for static verification; the user performs interactive validation.

## Packaging

- Do not add backend process management inside `main.js`; use `packaging/docker-entrypoint.sh` for container lifecycle.
- Keep renderer configuration build-time only (`VITE_BACKEND_URL`) aligned with container runtime expectation `http://127.0.0.1:8000`.
- Settings persistence must work with a bind-mounted directory and `APP_SETTINGS_DIR`.

## Documentation

Update this file and `README.md` for architecture, settings, controls, endpoint, packaging, or major behavior changes. Keep focused docs under `docs/` synchronized. Do not commit generated `dist/` or `node_modules/` changes.
