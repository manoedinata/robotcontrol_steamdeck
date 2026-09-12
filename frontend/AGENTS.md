# Frontend Guidance

## Purpose

This directory is the Steam Deck UI. Electron provides the desktop window, application lifecycle, atomic settings persistence, and Exit action. It must not own UDP sockets, camera relays, transcoding, packet encoding, or backend process supervision.

## Architecture

- `main.js`: BrowserWindow, application lifecycle, settings load/save IPC, and Exit IPC.
- `electron-components/preload.js`: narrow `quitApp`, `loadSettings`, and `saveSettings` bridge.
- `src/App.vue`: persistent command shell (Record/Recordings/Exit/Settings stack on the right, PTZ focus near/far buttons on the left), backend connection lifecycle, and Settings/Recordings page state.
- `src/views/HomeView.vue`: camera, UDP ping/battery telemetry, controller status, and control composition.
- `src/views/SettingsView.vue`: camera sources, UDP destination, velocity limits, and keyboard settings.
- `src/views/SettingsView.vue` reads `GET /storage/targets` for the recording destination picker. The operator selects a card, never a path; the stored value is the folder the backend reported for it.
- `src/views/RecordingsView.vue`: the recordings library -- a scrollable list of past sessions, each expandable into its files, with playback, save, and delete. Reads `GET /recordings`; opening one session reads `GET /recordings/<session>`.
- `src/components/RecordingsShell.vue`: the full-bleed page the library is shown in, mirroring `SettingsShell.vue`.
- `src/composables/useRecordings.js`: module-scoped library state, the open session, and the delete call.
- `src/composables/useRecordingsGamepadNavigation.js`: D-pad/A/B for that page, scoped to the player while it is open.
- `src/utils/formatRecording.js`: sizes, durations, session times, and the wording for a source that recorded nothing.
- `src/components/CameraFeed.vue`: one always-connected camera source, negotiated as backend WebRTC via `/offer?src=<id>`, with its own reconnect state. Every source kind arrives this way. `HomeView.vue` mounts one per source and shows only the active one.
- `src/components/ControllerPanel.vue`: Y/theta input mapping and generic packet updates.
- `src/composables/useBackendConnection.js`: singleton typed WebSocket transport, telemetry freshness, reconnect, replay, and backend WebRTC signaling URL.
- `src/composables/useControlState.js`: generic reactive command packet and frame-coalesced publication.
- `src/composables/useSettings.js`: shared persisted settings and backend config synchronization.
- `../packets-schema.json`: backend-owned binary UDP command layout.

## Required Invariants

### Electron

Keep `contextIsolation: true` and `nodeIntegration: false`. Do not expose generic IPC, filesystem, shell, network, process-spawn, or packet APIs. Keep settings writes as temporary-file plus rename.

In local development the backend is launched separately and is not supervised by Electron. In the Docker/Steam path, both processes run inside the same container and are supervised by `docker-entrypoint.sh`; Electron still must not spawn the backend itself.

Settings directory is configurable through `APP_SETTINGS_DIR` at runtime. Default to a path under the user's config home when the variable is absent, and use the provided path verbatim when present. This is required for Docker bind-mounts.

### Backend Connection

The default backend base URL is `http://127.0.0.1:8000`; `VITE_BACKEND_URL` may replace it at build/dev time. Keep `index.html` CSP aligned with allowed local WebSocket and stream endpoints. The recordings page plays video and shows poster frames from the backend origin, so `media-src` and `img-src` list it too -- the packaged app loads over `file://`, where `'self'` is not the backend.

WebSocket messages are separated by `type`:

- `{ "type": "record", "action": "start" | "stop" }` — records every configured source at once. Never replayed on reconnect: the backend owns whether a recording is running and pushes `{ "type": "recording", ... }` on connect and on every change.
- `{ "type": "config", "config": { "udp_host", "udp_port", "udp_listen_port", "camera_streams", "camera_backend", "ptz_ip", "recordings_dir" } }` — `camera_streams` is `[{ "id", "url" }]`, one entry per configured source. A url is `rtsp://`, `ws://`, or `wss://`; the backend owns all of them.
- `{ "type": "send", "packet": { ...schemaFields } }`
- `{ "type": "ptz", "direction", "zoom", "focus" }` — held PTZ requests; any field null when nothing is held.
- Backend telemetry uses `{ "type": "receive", "packet": { "battery_level": 0..100 } }`.
- On connect the backend announces the settable send fields as `{ "type": "schema", "fields": [{ "name", "role", "type", "min", "max", "default" }] }` (padding excluded). Settings renders one min/max row per entry; do not parse `packets-schema.json` in the renderer.
- Backend host reachability uses `{ "type": "ping", "ping_ms": number | null }`; the value measures ICMP latency to the configured UDP destination, not command-datagram RTT.
- Backend errors use `{ "type": "error", "message": "..." }`.

Reconnect automatically and replay latest config before latest control state. Components must not create their own sockets.

### Extending Controls

To add a command input, initialize its field in `useControlState.js`, bind the component through `updatePacket({ field: value })`, and add the ordered binary field to `../packets-schema.json`. Do not add field-specific transport handlers, IPC methods, or binary offsets. See [docs/udp.md#extending-the-udp-packet](docs/udp.md#extending-the-udp-packet) for the concrete three-step recipe.

Current control mapping remains:

- Left stick vertical axis controls `vy`; up is positive.
- Right stick horizontal axis controls `vtheta`; right is positive.
- Theta is negated before publishing when `vy` is negative, so steering stays driver-relative while reversing.
- Gamepad dead zone is `0.12`; pointer/touch has no dead zone.
- Each axis is scaled by the `packetLimits` entry of the send field carrying its role (`yVelocity`, `thetaVelocity`): the positive half of the stick reaches `max`, the negative half `min`, and the result is clamped into that range. Limits default to the schema bounds and may only narrow them.
- PTZ (D-pad rotate, LB/RB zoom, on-screen focus buttons) is gated by `useSettings().ptzControlsActiveCamera`: requests are sent only while `ptzIp`'s host equals the active camera stream's host. `usePTZState` publishes a stop and drops local state when that flips false; `App.vue` hides the focus buttons.

### Settings

Preserve this persisted contract:

```json
{
  "cameraSources": [
    { "type": "rtsp", "url": "rtsp://192.168.1.20:554/video", "username": "", "password": "" },
    { "type": "websocket", "url": "ws://192.168.1.21:8080", "username": "", "password": "" }
  ],
  "activeCameraIndex": 0,
  "cameraBackend": "go2rtc",
  "packetLimits": {
    "pwm": { "min": -100, "max": 100 },
    "steering": { "min": -100, "max": 100 }
  },
  "udpHost": "192.168.1.30",
  "udpPort": 5000,
  "udpListenPort": 8889,
  "useOnScreenKeyboard": true,
  "ptzIp": ""
}
```

`ptzIp` is the renderer source of truth for PTZ and is sent to the backend as `ptz_ip`. The PTZ camera credentials are not a setting: they are hardcoded in the backend (`PTZ_USERNAME`/`PTZ_PASSWORD` in `PTZController.py`).

Legacy top-level `cameraType`/`cameraUrl`/`cameraUsername`/`cameraPassword` files must keep loading as a single source and be rewritten into `cameraSources` on save.

Empty UDP host and port `0` disable transmission. The camera form supports RTSP and direct camera WebSocket mode, one row per source, with at least one row always present. RTSP preserves credentials in separate persisted fields and sends them only as URL-encoded userinfo inside that source's `camera_streams[].url`. Every source is synced to the backend and stays connected at once; `activeCameraIndex` only selects which warm feed the Home view shows, and B (Circle) cycles it with no reconnect. Stream ids are `cam-<sourceIndex>`. Keep `useSettings.js` as the renderer source of truth.

### Recordings

The library is read-only apart from delete. The renderer never reads a recording off disk and never builds a path: every URL comes from `recordingFileUrl()` in `useBackendConnection.js`, so the backend origin is defined in one place.

Playback is `GET .../play`, which remuxes Matroska to fragmented MP4 because Chromium cannot play Matroska. That response carries no index and honours no Range, so a `<video>` cannot seek in it: the page shows a start-from slider that re-opens the clip with `?t=<seconds>`, and the backend re-bases the stream to zero, so the elapsed time the player shows is relative to that jump. A part the backend reports as `playable: false` -- an MJPEG camera -- must never be mounted in a plain `<video>`; its button asks for `?transcode=1` outright, since the backend answers 415 otherwise. This device does not pay for an encode unless the operator asks.

An unavailable root is a `200` with `available: false`, not an error: render the "insert the card" empty state, never a failure. A session with `active: true` is still being written and must not offer Delete.

A source listed with `recorded: false` is the reason the page exists: it was configured, it was recording, and it produced no file. It is always shown, never filtered out.

### Camera

`HomeView.vue` renders one `CameraFeed.vue` per source from `useSettings().cameraFeeds`, keyed so an edited source remounts while a plain switch does not, and `v-show`s only the active one. Each `CameraFeed` holds its connection for its whole lifetime regardless of visibility, so switching never reconnects. It negotiates backend `/offer?src=<streamId>` for every source kind, whatever transport the backend uses upstream. An empty stream id shows idle. Errors retry every two seconds; transports close on prop change and unmount. The selectable `cameraBackend` setting applies to every source (`go2rtc` default, or `aiortc`). Do not silently fall back between backends, add Electron camera relays, or give the renderer a direct camera connection. Keeping every source warm scales CPU/GPU/bandwidth with the source count — a deliberate trade for instant switching.

### UI and Navigation

Keep the camera-first operational surface mounted while Settings opens as a drawer and Recordings opens as a full-bleed page over it. Preserve the 1280x800 target, 960x640 minimum, existing Bootstrap/Lucide/local Sass design, joystick dimensions, shell action focus, on-screen keyboard behavior, and Gamepad API ownership in `useGamepad.js`.

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
