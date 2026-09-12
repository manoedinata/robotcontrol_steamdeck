# Architecture

## Process Boundaries

### Local development

```text
Vue renderer                    Electron main                 FastAPI backend
-------------                   -------------                 ---------------
Camera/control UI               BrowserWindow                WS /ws/controls
Gamepad and touch input  <IPC>  Settings JSON                Binary UDP at 50 Hz
Typed WebSocket client          Exit/lifecycle               POST /offer?src= (all sources)
WebRTC <video> only, no camera code                       Camera WebSocket hub + relay
                                                          UDP telemetry receiver
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
                   |               +-- managed go2rtc on 127.0.0.1:1984 / WebRTC :8555
                   |       +-- wait for /health
                   |       +-- Electron frontend on same host
                   |
                   +-- /app/config bind-mounted from host
```

Electron has no robot or camera relay transport code, and neither does the renderer: every camera source is backend-owned and arrives as WebRTC. Electron exposes only `quitApp()`, `loadSettings()`, and `saveSettings(settings)` through a context-isolated preload. `nodeIntegration` remains disabled.

Vue owns input interpretation and UI state. `useBackendConnection.js` owns one WebSocket, reconnects every two seconds, replays latest configuration and control state after connection, and tracks live/stale telemetry. `useControlState.js` owns the packet object and coalesces reactive updates per animation frame. `useSettings.js` persists the frontend settings shape, including the telemetry listening port, keeps RTSP credentials separate from the source URL, and translates them into backend configuration.

FastAPI owns network configuration, schema-driven packet validation/encoding/decoding, the 50 Hz command task, the independently bound telemetry receiver, camera backend selection, and WebRTC signaling. Every configured source is registered and kept connected at once so the UI can switch instantly; `POST /offer?src=<id>` selects one. Direct camera WebSocket sources are held by `CameraWebSocketSource.py`, one connection per camera, and re-served at `GET /camera/<id>/stream` so the camera backend consumes them like any other input. The aiortc backend shares one connection per RTSP source between every peer and that relay, so recording reads the live view's session instead of dialing the camera again; go2rtc holds its connections in a child process, where recording cannot reach them. The default go2rtc backend is a FastAPI-managed localhost child process; aiortc remains an explicit in-process alternative. `packets-schema.json` at the repository root is the binary packet source of truth.

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
    useRecordings.js              Recordings library state and delete
  src/components/                 Camera, controls, Settings and Recordings shells, keyboard
  src/views/                      Home, Settings, and Recordings content
backend/
  server.py                       WebSocket, UDP send/receive, WebRTC signaling
  WebRTCStream.py                 Camera backend selection and SDP
  CameraWebSocketSource.py        Camera WebSocket hub and HTTP relay
  Recorder.py                     Records every source at once, writes session.json
  RecordingLibrary.py             Reads recordings back: list, play, thumbnail, delete
  utils.py                        Binary schema encoder/decoder
scripts/
  udp_server_simulation.py        Bidirectional command/telemetry simulator
  udp_telemetry_simulation.py     One-shot battery telemetry sender
packets-schema.json               Ordered command and telemetry layouts
```

The backend also serves the recordings library, which `RecordingsView.vue`
renders as a full-bleed page over the camera. `GET /recordings` fills the list;
expanding one session reads `GET /recordings/<session>` for the per-file detail
the list deliberately leaves out, because the backend does not probe files it was
only asked to enumerate.

The renderer never reads a recording off disk and never assembles a path: every
URL comes from `recordingFileUrl()` in `useBackendConnection.js`, so the backend
origin is defined once. Playback is `GET /recordings/<session>/<file>/play`,
which remuxes Matroska into fragmented MP4 on the fly, Chromium being unable to
play Matroska. That response carries no index and honours no Range, so a
`<video>` cannot seek in it; the player shows a start-from slider that re-opens
the clip with `?t=<seconds>`. The backend re-bases the stream to zero, so the
elapsed time the native controls show is relative to that jump and the offset is
displayed separately. A part reported as `playable: false` -- an MJPEG camera --
is never mounted in a plain `<video>`: its button asks for `?transcode=1`
outright, because the backend answers 415 otherwise and this device does not pay
for an encode unless the operator asks for one.

Playing and showing poster frames from the backend origin is why the CSP in
`frontend/index.html` lists it under `media-src` and `img-src` as well as
`connect-src`: the packaged app loads over `file://`, where `'self'` is not the
backend.

The backend is a separately launched local service. Electron does not spawn, restart, or terminate it.
