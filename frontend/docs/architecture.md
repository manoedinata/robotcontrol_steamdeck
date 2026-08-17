# Architecture

## Process Boundaries

```text
Vue renderer                    Electron main                 FastAPI backend
-------------                   -------------                 ---------------
Camera/control UI               BrowserWindow                WS /ws/controls
Gamepad and touch input  <IPC>  Settings JSON                Binary UDP at 50 Hz
Typed WebSocket client          Exit/lifecycle               GET /stream (MJPEG)
```

Electron has no robot or camera transport code. It exposes only `quitApp()`, `loadSettings()`, and `saveSettings(settings)` through a context-isolated preload. `nodeIntegration` remains disabled.

Vue owns input interpretation and UI state. `useBackendConnection.js` owns one WebSocket, reconnects every two seconds, and replays latest configuration and control state after connection. `useControlState.js` owns the packet object and coalesces reactive updates per animation frame. `useSettings.js` persists the existing frontend settings shape and translates it to backend config keys.

FastAPI owns network configuration, schema-driven packet validation/encoding, the 50 Hz UDP task, OpenCV camera capture, and shared MJPEG encoding. `packets-schema.json` at the repository root is the binary packet source of truth.

The camera and controller stay mounted when the Settings drawer opens, so transport and control state remain active. Backend disconnection is shown in the Home HUD and does not block local UI operation.

## Repository Map

```text
frontend/
  main.js                         Electron shell/settings
  electron-components/preload.js Restricted renderer bridge
  src/composables/
    useBackendConnection.js       WebSocket and stream endpoint
    useControlState.js            Generic packet state
    useSettings.js                Persisted settings/config sync
  src/components/                 Camera, controls, Settings shell, keyboard
  src/views/                      Home and Settings content
backend/
  server.py                       WebSocket, UDP, camera/MJPEG
  utils.py                        Binary schema encoder
packets-schema.json               Ordered UDP command layout
```

The backend is a separately launched local service. Electron does not spawn, restart, or terminate it.
