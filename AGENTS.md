# AGENTS.md

## Repository Purpose

This repository is a Steam Deck-oriented Electron and Vue application for viewing a robot camera and visualizing differential-drive inputs. The Home view displays an IP camera stream and calculates linear Y and angular theta velocity from Steam Deck, gamepad, pointer, or touch input. The Settings view persists the camera source.

The target viewport is 1280x800. The primary runtime environment is SteamOS Gaming Mode.

The application does not currently send velocity commands to a robot, ROS, or any network transport. Do not describe it as a complete robot control client until a command transport is implemented.

## Architecture

- `main.js`: Electron main process, BrowserWindow creation, IPC handlers, and settings filesystem access.
- `preload.js`: narrow context bridge exposed as `window.electronAPI`.
- `src/App.vue`: persistent sidebar shell and Exit action.
- `src/router/index.js`: eagerly imported Home and Settings routes using hash history. Hash history is required for production `file://` loading.
- `src/views/HomeView.vue`: camera state, `<img>` rendering, Gamepad API polling, pointer joystick handling, and velocity calculations.
- `src/views/SettingsView.vue`: camera URL field parsing and assembly.
- `src/composables/useSettings.js`: module-level reactive settings state shared between routes.
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

Do not access Node.js, Electron, or the filesystem directly from Vue code. Add narrowly scoped IPC methods in `main.js` and expose only those methods through `preload.js`. Validate all renderer-provided IPC payloads in the main process.

Keep application lifecycle and settings filesystem ownership in the main process. Do not broaden `window.electronAPI` with generic IPC, filesystem, or shell access.

### Settings

The current persisted contract is:

```json
{
  "cameraUrl": "http://192.168.1.20:8080/video"
}
```

`SettingsView.vue` presents protocol, IP, port, and subpath separately, then assembles one `cameraUrl`. `useSettings.js` keeps that URL reactive across views. Preserve compatibility with existing `settings.json` files when changing this schema.

The current form offers HTTP and RTSP only. Its parser does not preserve URL query parameters or fragments. Account for that limitation when changing URL handling; do not silently claim full arbitrary-URL editing or HTTPS form support.

Settings writes use a temporary file followed by `fs.rename`. Retain atomic-style writes for future settings.

Keep `useSettings.js` as the single shared renderer source of truth unless application state becomes complex enough to justify a dedicated store. Route views should consume the composable rather than passing settings through `App.vue` props and events.

### Camera

`HomeView.vue` currently uses `<img>` so HTTP/HTTPS snapshots and MJPEG streams work without an additional player. Do not claim that direct RTSP playback works: Chromium cannot render `rtsp://` in `<img>`. RTSP support requires a relay or transcoder. HLS or WebRTC output would also require a corresponding browser renderer rather than the current `<img>` implementation.

The Content Security Policy in `index.html` must continue to permit required external camera image sources. Keep camera diagnostic logs under the `[camera]` prefix.

### Robot Controls

The application models a differential-drive robot:

- Left stick vertical axis (`axes[1]`) controls Y velocity.
- Up is positive and down is negative.
- Right stick horizontal axis (`axes[2]`) controls theta velocity.
- Left is negative and right is positive.
- Both outputs are normalized and capped at `-10` through `+10`.
- A `0.12` dead zone is applied to hardware gamepad input; pointer and touch input currently have no dead zone.
- Sideways translation is intentionally absent.

Pointer and touch controls must follow the same axis constraints as hardware input. Do not add X translation without an explicit product requirement.

### UI Layout

The application must fit the Steam Deck's 1280x800 viewport without page scrolling. Preserve:

- The persistent vertical sidebar.
- A flexible camera region above a compact joystick row.
- Stable joystick dimensions and puck travel.
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

## Known Gaps

- Velocity commands are not sent to robot middleware or a network endpoint.
- RTSP can be persisted but is not directly playable by the current renderer.
- Camera credentials are unsupported.
- The Settings form does not expose HTTPS or preserve URL query parameters and fragments.
- The one-row Settings layout needs verification at the 960x640 minimum window size.
- Automated tests, linting, installers, packaging, and release automation are not configured.
