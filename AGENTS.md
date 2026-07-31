# AGENTS.md

## Repository Purpose

This repository is a Steam Deck-oriented Electron and Vue application for viewing a robot camera and visualizing differential-drive inputs. The Home view displays an IP camera stream and calculates linear Y and angular theta velocity from Steam Deck, gamepad, pointer, or touch input. The Settings view persists the camera source.

The target viewport is 1280x800. The primary runtime environment is SteamOS Gaming Mode.

The main process can send packed Y/theta velocity datagrams over UDP, but the controller output is not yet connected to a destination or transmission loop. Do not describe the application as a complete robot control client until that integration is implemented.

## Architecture

- `main.js`: Electron main process, BrowserWindow creation, IPC handlers, settings filesystem access, and RTSP transcoder lifecycle.
- `preload.js`: narrow context bridge exposed as `window.electronAPI`.
- `rtsp-transcoder.js`: one-source RTSP/TCP to MJPEG relay using bundled `ffmpeg`, bound to a tokenized loopback URL.
- `udp-client.js`: validates and packs a 3-byte ASCII header plus two little-endian signed 32-bit velocities into an 11-byte UDP datagram.
- `src/App.vue`: persistent sidebar shell and Exit action.
- `src/router/index.js`: eagerly imported Home and Settings routes using hash history. Hash history is required for production `file://` loading.
- `src/views/HomeView.vue`: thin composition shell that renders `CameraFeed` and `ControllerPanel` inside the Home layout.
- `src/components/CameraFeed.vue`: camera state, `<img>` rendering, and the status overlay/indicator. Consumes `useSettings` directly.
- `src/components/ControllerPanel.vue`: Gamepad API polling, dead zone, per-axis velocity math, and the joystick/status layout.
- `src/components/Joystick.vue`: reusable single-axis joystick (`v-model` position, `axis` prop, pointer/touch drag with `drag-start`/`drag-end` events).
- `src/components/OnScreenKeyboard.vue`: modal Settings keyboard with IP, integer, decimal, and text layouts plus commit/cancel behavior.
- `src/views/SettingsView.vue`: camera URL field parsing/assembly, per-axis velocity-limit fields, and the keyboard preference toggle.
- `src/composables/useSettings.js`: module-level reactive settings state (camera URL, velocity limits, and keyboard preference) shared between routes.
- `src/styles.css`: global layout optimized for a 1280x800 display.
- `settings.json`: local runtime configuration stored beside `launch.sh`.

### Runtime Modes

- `npm run dev`: runs Vite on `127.0.0.1:5173`, waits for it, and launches Electron with `VITE_DEV_SERVER_URL`.
- `npm run build`: produces the renderer in `dist/`.
- `npm run electron`: launches Electron against an existing `dist/index.html`.
- `npm run start`: builds and then launches Electron.
- `launch.sh`: launches Electron's native binary for Steam Gaming Mode and requires installed dependencies plus an existing build.

Browser preview is suitable for renderer layout checks, but settings persistence and the Exit action require the Electron preload bridge.

## Required Invariants

### Electron Security

Keep these BrowserWindow settings:

- `contextIsolation: true`
- `nodeIntegration: false`

Do not access Node.js, Electron, or the filesystem directly from Vue code. Add narrowly scoped IPC methods in `main.js` and expose only those methods through `preload.js`. Validate renderer-provided IPC payloads in the main process unless a specific IPC contract, such as settings persistence, intentionally accepts renderer-owned structured data unchanged.

Keep application lifecycle and settings filesystem ownership in the main process. Do not broaden `window.electronAPI` with generic IPC, filesystem, or shell access.

The UDP preload contract is `sendUdpMessage(host, port, header, velocity)`, where `header` is exactly three ASCII characters and `velocity` is `[yVelocity, thetaVelocity]`. The main process validates the host and port; `udp-client.js` validates and serializes both signed 32-bit integers. Keep the packet packed with Y at byte offset `3`, theta at byte offset `7`, and little-endian byte order unless the receiver protocol changes explicitly. Close the UDP socket during application shutdown.

### Settings

The current persisted contract is:

```json
{
  "cameraUrl": "http://192.168.1.20:8080/video",
  "maxYVelocity": 10,
  "maxThetaVelocity": 10,
  "useOnScreenKeyboard": true
}
```

`SettingsView.vue` presents protocol, IP, port, and subpath separately, then assembles one `cameraUrl`. It also exposes `maxYVelocity` and `maxThetaVelocity` as separate numeric fields. `useSettings.js` keeps the URL and both limits reactive across views. Preserve compatibility with existing `settings.json` files when changing this schema.

`maxYVelocity` and `maxThetaVelocity` are the per-axis velocity caps (default `10`). `useSettings.js` owns these defaults and applies them when stored keys are missing. The main process persists and returns the Settings IPC payload unchanged; validation constraints are expressed by the Settings form before it sends the payload.

`useOnScreenKeyboard` defaults to `true` in `useSettings.js` for existing files where it is missing. When enabled, Settings inputs must remain read-only with `inputmode="none"` and open `OnScreenKeyboard.vue`; this prevents Steam keyboard events from modifying native fields. Disabling it restores native editing for physical keyboards. The app cannot and must not claim to disable Steam's global `Steam + X` overlay. Preserve Done-as-commit and Cancel-without-change semantics.

The current form offers HTTP and RTSP only. Its parser does not preserve URL query parameters or fragments. Account for that limitation when changing URL handling; do not silently claim full arbitrary-URL editing or HTTPS form support.

Settings writes use a temporary file followed by `fs.rename`. Retain atomic-style writes for future settings.

Keep `useSettings.js` as the single shared renderer source of truth unless application state becomes complex enough to justify a dedicated store. Route views should consume the composable rather than passing settings through `App.vue` props and events.

### Camera

`CameraFeed.vue` uses `<img>` so HTTP/HTTPS snapshots and MJPEG streams work directly. Chromium cannot render `rtsp://` in `<img>`; RTSP sources are resolved through the Electron main process and `rtsp-transcoder.js`. The relay runs bundled `ffmpeg` over RTSP/TCP, removes audio, scales within 1280x800 using `fast_bilinear`, limits output to 15 fps, uses two encoder threads, and serves JPEG quality `7` as MJPEG from a tokenized random port bound only to `127.0.0.1`. Low-latency input flags minimize buffering and probe delay.

Keep RTSP process ownership in the main process. Permit only HTTP, HTTPS, and RTSP camera protocols; do not expose process arguments, generic process spawning, or a network-accessible relay through the preload bridge. Preserve one active source, terminate the child process on source changes and application quit, and avoid logging camera URLs because they may contain credentials. HLS or WebRTC output would require a corresponding browser renderer rather than the current `<img>` implementation.

The Content Security Policy in `index.html` must continue to permit required external camera image sources. Keep camera diagnostic logs under the `[camera]` prefix.

### Robot Controls

The application models a differential-drive robot:

- Left stick vertical axis (`axes[1]`) controls Y velocity.
- Up is positive and down is negative.
- Right stick horizontal axis (`axes[2]`) controls theta velocity.
- Left is negative and right is positive.
- Each stick is normalized to `-1..+1`, then scaled by its per-axis limit (`maxYVelocity` / `maxThetaVelocity`, default `10`). Output is capped at the negative and positive limit.
- `ControllerPanel.vue` reads the limits from `useSettings.js`, so velocity readouts update reactively when Settings change.
- A `0.12` dead zone is applied to hardware gamepad input; pointer and touch input currently have no dead zone.
- Sideways translation is intentionally absent.

Pointer and touch controls must follow the same axis constraints as hardware input. Do not add X translation without an explicit product requirement.

### UI Layout

The application must fit the Steam Deck's 1280x800 viewport without page scrolling. Preserve:

- The persistent vertical sidebar.
- A flexible camera region above a compact joystick row.
- Stable joystick dimensions and puck travel.
- Touch- and controller-focusable built-in keyboard keys with stable dimensions.
- One-row camera settings at the target viewport.
- Usable responsive behavior down to the Electron window minimum of 960x640. The current Settings row needs explicit verification at that minimum and may require responsive work.

Use existing Bootstrap controls, Lucide icons, and local CSS conventions. Avoid adding a second design system.

### Routing And Views

- Keep route-level screens in `src/views/` and application chrome in `src/App.vue`.
- Use `RouterLink` and `RouterView`; do not reintroduce manual `v-if` page switching.
- Keep `createWebHashHistory()` while production is loaded through `BrowserWindow.loadFile()`.
- Home and Settings are intentionally eager imports so the offline Electron renderer ships as one JavaScript application bundle.

## Validation

The Vite 8 toolchain requires Node.js `20.19+` or `22.12+`.

```bash
npm install
npm run dev
npm run build
npm run start
```

- `npm run build` is the minimum required validation.
- Use `npm run dev` for interactive renderer work.
- Use `npm run start` or `launch.sh` to check production `file://` loading.
- Camera and gamepad behavior should be verified interactively in Electron on target hardware when practical.

There are currently no automated test or lint scripts. Do not report tests or lint as passing unless those scripts are added and executed.

## Change Guidance

- Read the current file contents before editing; this repository may contain uncommitted user changes.
- Keep changes scoped and preserve the existing Vue Composition API style.
- Prefer shared reactive state in `useSettings.js` over prop drilling between routed views.
- Keep route history hash-based unless the Electron production loading strategy changes.
- Preserve the camera settings JSON contract unless a compatible migration is included.
- Do not commit generated `dist/` or `node_modules/` content.
- Do not overwrite a user's local camera URL in `settings.json` while performing unrelated work.
- Run `npm run build` after renderer, Electron, configuration, or documentation-adjacent command changes.
- Keep changes focused; do not introduce a state library, second UI framework, or stream player without a concrete requirement.
- On any major change (new feature, architecture shift, settings-schema change, control/behavior change), update both `README.md` and this `AGENTS.md` in the same change so the docs stay in sync with the code.

## Known Gaps

- The packed UDP sender is not yet connected to controller output, destination settings, or a transmission loop.
- RTSP is supported through the main-process MJPEG relay; Chromium still cannot render `rtsp://` directly.
- Camera credentials are not represented by the Settings form, although manually persisted URL credentials can be passed to `ffmpeg`.
- RTSP transcoding supports one source, outputs MJPEG at 15 fps, and may be CPU intensive.
- RTSP connection and authentication errors are currently available through `ffmpeg` diagnostics rather than structured renderer status.
- The Settings form does not expose HTTPS or preserve URL query parameters and fragments.
- The built-in keyboard cannot disable Steam's global keyboard overlay; it only protects the Settings inputs from native keyboard insertion while enabled.
- The one-row Settings layout needs verification at the 960x640 minimum window size.
- Automated tests, linting, installers, packaging, and release automation are not configured.
