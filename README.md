# Steam Deck Robot Monitor

A Steam Deck-oriented Electron application for viewing a robot camera and visualizing differential-drive inputs. The interface targets the Deck's 1280x800 display and combines an IP camera feed with live velocity values derived from hardware or on-screen joysticks.

> **Project status:** this repository currently implements the local monitor and input UI. It calculates Y and theta velocity values, but it does not send commands to a robot, ROS, or another middleware transport.

## What It Does

- Displays an HTTP image or MJPEG camera stream.
- Reads Steam Deck or compatible controller input through the browser Gamepad API.
- Maps the left stick vertical axis to linear Y velocity from `-10` to `+10`.
- Maps the right stick horizontal axis to angular theta velocity from `-10` to `+10`.
- Supports pointer and touch dragging for both on-screen joystick controls.
- Stores the camera source in `settings.json` beside the launcher.
- Runs as a frameless Electron application with Home and Settings routes plus an in-app Exit action.
- Uses a layout sized to fit the Steam Deck display without scrolling.

## Stack

- Electron
- Vue 3 with the Composition API
- Vue Router using hash history
- Vite
- Bootstrap 5
- Lucide icons

## Requirements

- Node.js `20.19+` or `22.12+`, and npm (required by Vite 8)
- Linux or another Electron-supported desktop operating system
- An HTTP snapshot or MJPEG endpoint reachable from the device
- A browser-visible gamepad for hardware input; pointer and touch controls also work

The application was built primarily for SteamOS and Steam Deck Gaming Mode.

## Quick Start

Install dependencies, start Vite, wait for it to become available, and launch Electron:

```bash
npm install
npm run dev
```

Vite listens on `http://127.0.0.1:5173`. Electron waits for that URL and then loads it through `VITE_DEV_SERVER_URL`.

## Commands

| Command | Purpose |
| --- | --- |
| `npm run dev` | Run Vite and Electron together for development |
| `npm run build` | Build the renderer into `dist/` |
| `npm run electron` | Launch Electron using the existing `dist/` build |
| `npm run start` | Build the renderer, then launch Electron |
| `npm run preview` | Preview the production renderer in a browser |

The browser preview is useful for layout work, but Electron-only operations such as loading, saving, and quitting depend on `window.electronAPI`. Use Electron to verify the complete workflow.

There is currently no automated test or lint script. Run `npm run build` as the minimum validation after changes.

## Steam Deck Launch

`launch.sh` is intended for Steam Gaming Mode. It launches Electron's native binary directly instead of relying on the Node-based Electron CLI shim.

Before running it, install dependencies and create a production build:

```bash
npm install
npm run build
./launch.sh
```

The script:

- Resolves all paths relative to the repository directory.
- Verifies that Electron and `dist/index.html` exist.
- Clears `LD_PRELOAD`, which can interfere with Electron in Gaming Mode.
- Launches `node_modules/electron/dist/electron` directly.

To add the application to Steam, add `launch.sh` as a non-Steam game and ensure it is executable:

```bash
chmod +x launch.sh
```

## Camera Configuration

Open **Settings** and provide:

- **Stream type:** HTTP or RTSP
- **Source IP:** for example, `192.168.1.20`
- **Port:** for example, `8080`
- **Subpath:** optional, for example, `/video`

The form assembles these fields into one URL and saves it in `settings.json`:

```json
{
  "cameraUrl": "http://192.168.1.20:8080/video"
}
```

The Electron main process owns the file. The Vue views share its current value through `useSettings.js`; writes use a temporary file followed by rename to reduce the chance of a partially written settings file.

The current Settings form exposes HTTP and RTSP and reconstructs the URL from protocol, host, port, and path. It does not preserve query parameters or fragments. If a camera URL needs HTTPS or query-string credentials/options, the current form must be extended before it can safely edit that URL.

### Supported Camera Streams

The Home view renders the camera with an HTML `<img>` element. Directly supported sources are therefore:

- HTTP or HTTPS snapshots
- HTTP or HTTPS MJPEG streams

Although RTSP can be selected and saved, Chromium cannot display an `rtsp://` URL directly in an `<img>` element. RTSP cameras require an external relay or transcoder that exposes a browser-compatible stream. The current `<img>` renderer supports snapshots and MJPEG; adding HLS or WebRTC would also require an appropriate renderer. No relay is included in this repository.

Camera lifecycle and failure details are logged to the Electron renderer console with the `[camera]` prefix.

## Controls

| Input | Robot value |
| --- | --- |
| Left stick up | Positive Y velocity, up to `+10` |
| Left stick down | Negative Y velocity, down to `-10` |
| Right stick left | Negative theta velocity, down to `-10` |
| Right stick right | Positive theta velocity, up to `+10` |

The Gamepad API mapping currently reads:

- Left stick Y from `axes[1]`
- Right stick X from `axes[2]`

A `0.12` dead zone is applied to hardware gamepad values before they are normalized. Mouse and touch interaction use the same constrained axes but do not apply that dead zone. Sideways translation is intentionally not represented because the target is a differential-drive robot.

## Architecture

```text
.
├── main.js                    Electron main process and settings IPC
├── preload.js                 Restricted renderer bridge
├── launch.sh                  Steam Gaming Mode production launcher
├── settings.json              Persisted local camera URL
├── index.html                 Renderer entry and Content Security Policy
├── vite.config.js             Vue/Vite production configuration
└── src
    ├── App.vue                Sidebar shell and application exit action
    ├── main.js                Vue, Bootstrap, and router initialization
    ├── styles.css             Steam Deck-oriented application styles
    ├── composables
    │   └── useSettings.js     Shared reactive settings state
    ├── router
    │   └── index.js           Eager Home/Settings routes using hash history
    └── views
        ├── HomeView.vue       Camera, gamepad polling, and joystick UI
        └── SettingsView.vue   Camera source form and persistence
```

### Electron Security Boundary

The renderer runs with:

- `contextIsolation: true`
- `nodeIntegration: false`

`preload.js` exposes only three operations through `window.electronAPI`:

- `quitApp()`
- `loadSettings()`
- `saveSettings(settings)`

Keep filesystem access and application lifecycle operations in the main process. Do not expose Node.js or Electron modules directly to Vue components.

### Renderer Data Flow

- `App.vue` owns the persistent shell and renders routed pages through `RouterView`.
- `HomeView.vue` and `SettingsView.vue` are route-level components, not manually toggled page components.
- `useSettings.js` owns one module-level reactive camera URL shared across routes.
- Vue code requests persistence through the preload bridge; only the Electron main process reads or writes `settings.json`.
- Hash history is intentional because production loads `dist/index.html` from `file://` without an HTTP server.

## Current Limitations

- Velocity values are visualized but are not sent to a robot transport or middleware.
- RTSP playback needs an external browser-compatible relay.
- Camera authentication is not represented in the Settings form.
- The Settings form does not expose HTTPS or preserve URL query parameters and fragments.
- Gamepad axis indices assume a conventional Steam Deck/gamepad mapping.
- There are no automated tests, lint rules, installers, or packaged release artifacts yet.

## Contributing

Read [AGENTS.md](AGENTS.md) before making architectural changes. Preserve the secure Electron preload boundary and validate changes with:

```bash
npm run build
```

For camera or controller changes, also test interactively in Electron on the target Steam Deck when possible.
