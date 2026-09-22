# Frontend Guidance

## Purpose

This directory is the Steam Deck UI. Electron provides the desktop window, application lifecycle, atomic settings persistence, and Exit action. It must not own UDP sockets, camera relays, transcoding, packet encoding, or backend process supervision.

## Architecture

- `main.js`: BrowserWindow, application lifecycle, settings load/save IPC, and Exit IPC.
- `electron-components/preload.js`: narrow `quitApp`, `readDeckBattery`, `loadSettings`, and `saveSettings` bridge.
- `src/views/HomeView.vue`: the camera-first HUD -- telemetry bar, the pan/tilt speed slider and the distance travelled docked beneath it, recording and battery readouts, and the controller panel.
- `src/App.vue`: persistent command shell (Record/Recordings/Exit/Settings stack on the right, PTZ focus near/far buttons, the infrared light toggle and the drive-direction toggle on the left), backend connection lifecycle, and Settings/Recordings page state.
- `src/views/HomeView.vue`: camera, UDP ping/battery telemetry, controller status, and control composition. The battery readout shows one of two sources and is tapped to change which.
- `src/composables/useDeckBattery.js`: the Deck's own battery, polled from the main process; the renderer has no other host-hardware reader.
- `src/composables/useOdometry.js`: differential-drive odometry over the two reported wheel speeds, integrated against the time between packets -- pose (`x`, `y`, `theta`), the signed distance travelled, and `reset()`, which Home binds to a hold on the distance readout. Only the distance is on screen; the pose is logged. A gap longer than `MAX_STEP_S` is skipped rather than integrated, since a missed speed is movement that cannot be recovered. It lives here, not in the backend, because the wire carries counts and the conversion needs the operator's own measurements of their robot.
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
- `src/composables/useDriveMode.js`: forward or reverse for the drive axis, shared between the shell's toggle and the controller; in-memory and always forward at start.
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
- `{ "type": "camera", "streams": ["cam-0"] }` — backend-pushed: these sources were just re-dialed because a url, a credential, `camera_backend` or `rtsp_transport` changed. The named feeds reconnect; the renderer must not reconnect a feed when settings are saved, which would race the config it just sent.
- `{ "type": "config", "config": { "udp_host", "udp_port", "udp_listen_port", "camera_streams", "camera_backend", "rtsp_transport", "ptz_ip", "recordings_dir" } }` — `camera_streams` is `[{ "id", "url" }]`, one entry per configured source. A url is `rtsp://`, `ws://`, or `wss://`; the backend owns all of them.
- `{ "type": "send", "packet": { ...schemaFields } }`
- `{ "type": "ptz", "direction", "zoom", "focus", "speed_multiplier" }` — held PTZ requests; any of the first three null when nothing is held. `speed_multiplier` is the pan/tilt speed step (1-6) and rides along on every one of these, held or not, so the slider reaches a camera that is already moving.
- `{ "type": "ptz_light", "on": true | false }` — the camera's infrared light. Latched, not held: sent once per press and never replayed on reconnect, because the backend owns the state and pushes `{ "type": "ptz_light", ... }` on connect and whenever any UI changes it.
- Backend telemetry uses `{ "type": "receive", "packet": { "position_left", "position_right", "speed_left", "speed_right" } }` -- every field of `packet_types.receive`. Frames 3 and 4 are the wheel speeds, which `useOdometry` holds over the gap between packets and integrates into a pose; Home shows the signed distance travelled, and the pose is logged to the console once a second while what else to do with it is decided. Frames 1 and 2, the absolute positions, are decoded and carried but nothing reads them now that the odometry is trusted. Fields are read one at a time, so one the robot does not send is null rather than a reason to drop the packet -- including `battery_level`, which this robot does not report at all.
- On connect the backend announces the settable send fields as `{ "type": "schema", "fields": [{ "name", "role", "type", "min", "max", "default" }] }` (padding excluded). Settings renders one min/max row per entry; do not parse `packets-schema.json` in the renderer.
- Backend host reachability uses `{ "type": "ping", "ping_ms": number | null }`; the value measures ICMP latency to the configured UDP destination, not command-datagram RTT.
- Backend errors use `{ "type": "error", "message": "..." }`.

Reconnect automatically and replay latest config before latest control state. Components must not create their own sockets.

### Extending Controls

To add a command input, initialize its field in `useControlState.js`, bind the component through `updatePacket({ field: value })`, and add the ordered binary field to `../packets-schema.json`. Do not add field-specific transport handlers, IPC methods, or binary offsets. See [docs/udp.md#extending-the-udp-packet](docs/udp.md#extending-the-udp-packet) for the concrete three-step recipe.

Current control mapping remains:

- Left stick vertical axis controls `vy` and travels up only, in both pointer and hardware input. Direction is `useDriveMode()`: forward publishes the travel positive, reverse publishes it negated. The mode lives in the renderer, is not persisted, and never reaches the wire as a field of its own -- the backend only ever sees the signed velocity.
- Right stick horizontal axis controls `vtheta`; right is positive.
- Theta is negated before publishing when `vy` is negative, so steering stays driver-relative while reversing. In reverse mode that is every non-zero push.
- Gamepad dead zone is `0.12`; pointer/touch has no dead zone.
- Each axis is scaled by the `packetLimits` entry of the send field carrying its role (`yVelocity`, `thetaVelocity`): the positive half of the stick reaches `max`, the negative half `min`, and the result is clamped into that range. Limits default to the schema bounds and may only narrow them.
- Face buttons on Home: A switches the drive direction, X focuses the camera (tap nearer, hold further), Y switches the infrared light, B switches the camera source. A and B still reach overlays as `activate`/`cancel`, so each Home binding yields while one is open; X and Y are read from `useGamepad().faceButtons`, which is live state rather than an event, because the tap-versus-hold split needs to know how long a button is down.
- PTZ (D-pad rotate, LB/RB zoom, X or the on-screen buttons to focus) is gated by `useSettings().ptzControlsActiveCamera`: requests are sent only while `ptzIp`'s host equals the active camera stream's host. `usePTZState` publishes a stop and drops local state when that flips false; `App.vue` hides the focus and light buttons. The light itself is not dropped with them: it is a camera setting the operator left on, not a button they are holding.

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
  "rtspTransport": "tcp",
  "packetLimits": {
    "pwm": { "min": -100, "max": 100 },
    "steering": { "min": -100, "max": 100 }
  },
  "udpHost": "192.168.1.30",
  "udpPort": 5000,
  "udpListenPort": 8889,
  "useOnScreenKeyboard": true,
  "ptzIp": "",
  "ptzSpeedMultiplier": 1,
  "distancePerCount": 1,
  "wheelSeparation": 0.5
}
```

`ptzIp` is the renderer source of truth for PTZ and is sent to the backend as `ptz_ip`. `ptzSpeedMultiplier` (1-6) is the only setting written from outside the Settings form -- the Home slider saves it on release, merged over the settings last read, since the file is rewritten whole. Any form that saves the file must carry it through for the same reason. The PTZ camera credentials are not a setting: they are hardcoded in the backend (`PTZ_USERNAME`/`PTZ_PASSWORD` in `PTZController.py`).

Legacy top-level `cameraType`/`cameraUrl`/`cameraUsername`/`cameraPassword` files must keep loading as a single source and be rewritten into `cameraSources` on save.

Empty UDP host and port `0` disable transmission. The camera form supports RTSP and direct camera WebSocket mode, one row per source, with at least one row always present. RTSP preserves credentials in separate persisted fields and sends them only as URL-encoded userinfo inside that source's `camera_streams[].url`. Every source is synced to the backend and stays connected at once; `activeCameraIndex` only selects which warm feed the Home view shows, and B (Circle) cycles it with no reconnect. Stream ids are `cam-<sourceIndex>`. Keep `useSettings.js` as the renderer source of truth.

### Recordings

The library is read-only apart from delete. The renderer never reads a recording off disk and never builds a path: every URL comes from `recordingFileUrl()` in `useBackendConnection.js`, so the backend origin is defined in one place.

Playback is `GET .../play`, which remuxes Matroska to fragmented MP4 because Chromium cannot play Matroska. That response carries no index and honours no Range, so a `<video>` cannot seek in it: the page shows a start-from slider that re-opens the clip with `?t=<seconds>`, and the backend re-bases the stream to zero, so the elapsed time the player shows is relative to that jump. A part the backend reports as `playable: false` -- an MJPEG camera -- must never be mounted in a plain `<video>`; its button asks for `?transcode=1` outright, since the backend answers 415 otherwise. This device does not pay for an encode unless the operator asks.

An unavailable root is a `200` with `available: false`, not an error: render the "insert the card" empty state, never a failure. A session with `active: true` is still being written and must not offer Delete.

A source listed with `recorded: false` is the reason the page exists: it was configured, it was recording, and it produced no file. It is always shown, never filtered out.

### Camera

`HomeView.vue` renders one `CameraFeed.vue` per source from `useSettings().cameraFeeds`, keyed by stream id, and `v-show`s only the active one. Each `CameraFeed` holds its connection for its whole lifetime regardless of visibility, so switching never reconnects. It negotiates backend `/offer?src=<streamId>` for every source kind, whatever transport the backend uses upstream. An empty stream id shows idle. A failed feed shows the error state with a "Hubungkan lagi" button and retries in the background every four seconds; either path re-offers (with `?restart=1`). Transports close on prop change and unmount.

A feed is `connected` only once video actually arrives (a receiver frame count or the `<video>` element's `playing`), never on `ontrack` alone, and five seconds without a frame is treated the same as any other failure -- the error state (button plus background retry), not a silent re-offer. Do not restart a feed from the settings form: reconnects that a config change causes are driven by the backend's `camera` message, so the offer cannot race the config. Count frames at the receiver, not at the element — every feed but one is hidden. The selectable `cameraBackend` setting applies to every source (`go2rtc` default, or `aiortc`), and `rtspTransport` (`tcp` default, or `udp`) selects how aiortc dials RTSP; recording stays on TCP regardless. Do not silently fall back between backends, add Electron camera relays, or give the renderer a direct camera connection. Keeping every source warm scales CPU/GPU/bandwidth with the source count — a deliberate trade for instant switching.

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
