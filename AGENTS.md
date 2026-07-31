# AGENTS.md

## Repository Purpose

This repository is a Steam Deck-oriented Electron and Vue application for monitoring a differential-drive robot. The Home view displays an IP camera stream and visualizes linear Y and angular theta velocity from the Steam Deck controls. The Settings view persists the camera source.

The target viewport is 1280x800. The primary runtime environment is SteamOS Gaming Mode.

## Architecture

- `main.js`: Electron main process, BrowserWindow creation, IPC handlers, and settings filesystem access.
- `preload.js`: narrow context bridge exposed as `window.electronAPI`.
- `src/App.vue`: persistent sidebar shell and Exit action.
- `src/router/index.js`: hash-based Home and Settings routes. Hash history is required for production `file://` loading.
- `src/views/HomeView.vue`: camera state, `<img>` rendering, Gamepad API polling, pointer joystick handling, and velocity calculations.
- `src/views/SettingsView.vue`: camera URL field parsing and assembly.
- `src/composables/useSettings.js`: module-level reactive settings state shared between routes.
- `src/styles.css`: global layout optimized for a 1280x800 display.
- `settings.json`: local runtime configuration stored beside `launch.sh`.

## Required Invariants

### Electron Security

Keep these BrowserWindow settings:

- `contextIsolation: true`
- `nodeIntegration: false`

Do not access Node.js, Electron, or the filesystem directly from Vue code. Add narrowly scoped IPC methods in `main.js` and expose only those methods through `preload.js`. Validate all renderer-provided IPC payloads in the main process.

### Settings

The current persisted contract is:

```json
{
  "cameraUrl": "http://192.168.1.20:8080/video"
}
```

`SettingsView.vue` presents protocol, IP, port, and subpath separately, then assembles one `cameraUrl`. `useSettings.js` keeps that URL reactive across views. Preserve compatibility with existing `settings.json` files when changing this schema.

Settings writes use a temporary file followed by `fs.rename`. Retain atomic-style writes for future settings.

### Camera

`HomeView.vue` currently uses `<img>` so HTTP snapshots and MJPEG streams work without an additional player. Do not claim that direct RTSP playback works: Chromium cannot render `rtsp://` in `<img>`. RTSP support requires a relay or transcoder and a browser-compatible output such as MJPEG, HLS, or WebRTC.

The Content Security Policy in `index.html` must continue to permit required external camera image sources. Keep camera diagnostic logs under the `[camera]` prefix.

### Robot Controls

The application models a differential-drive robot:

- Left stick vertical axis (`axes[1]`) controls Y velocity.
- Up is positive and down is negative.
- Right stick horizontal axis (`axes[2]`) controls theta velocity.
- Left is negative and right is positive.
- Both outputs are normalized and capped at `-10` through `+10`.
- A `0.12` dead zone is applied.
- Sideways translation is intentionally absent.

Pointer and touch controls must follow the same axis constraints as hardware input. Do not add X translation without an explicit product requirement.

### UI Layout

The application must fit the Steam Deck's 1280x800 viewport without page scrolling. Preserve:

- The persistent vertical sidebar.
- A flexible camera region above a compact joystick row.
- Stable joystick dimensions and puck travel.
- One-row camera settings at the target viewport.
- Responsive behavior down to the Electron window minimum of 960x640.

Use existing Bootstrap controls, Lucide icons, and local CSS conventions. Avoid adding a second design system.

## Development Commands

```bash
npm install
npm run dev
npm run build
npm run start
```

- `npm run dev` starts Vite on `127.0.0.1:5173`, waits for it, and launches Electron.
- `npm run build` is the minimum required validation.
- `npm run start` builds and launches the production renderer.
- `launch.sh` is the Steam Gaming Mode entry point and expects dependencies plus `dist/index.html` to exist.

There are currently no automated test or lint scripts. Do not report tests or lint as passing unless those scripts are added and executed.

## Change Guidance

- Read the current file contents before editing; this repository may contain uncommitted user changes.
- Keep changes scoped and preserve the existing Vue Composition API style.
- Prefer shared reactive state in `useSettings.js` over prop drilling between routed views.
- Keep route history hash-based unless the Electron production loading strategy changes.
- Do not commit generated `dist/` or `node_modules/` content.
- Do not overwrite a user's local camera URL in `settings.json` while performing unrelated work.
- Run `npm run build` after renderer, Electron, configuration, or documentation-adjacent command changes.
- Interactive camera and gamepad behavior should be verified in Electron on the target device when practical.

## Known Gaps

- Velocity commands are not sent to robot middleware or a network endpoint.
- RTSP is configurable but not directly playable.
- Camera credentials are unsupported.
- Automated tests, linting, packaging, and release automation are not configured.
