# Steam Deck Robot Monitor

A compact Electron application for monitoring and controlling a differential-drive robot from a Steam Deck. The interface is designed around the Deck's 1280x800 display and combines an IP camera feed with live gamepad-driven velocity controls.

## What It Does

- Displays an HTTP image or MJPEG camera stream.
- Reads the Steam Deck controls through the browser Gamepad API.
- Maps the left stick vertical axis to linear Y velocity from `-10` to `+10`.
- Maps the right stick horizontal axis to angular theta velocity from `-10` to `+10`.
- Supports pointer and touch dragging for both on-screen joystick controls.
- Stores the camera source in `settings.json` beside the launcher.
- Runs as a frameless Electron application with an in-app Exit button.
- Uses a layout sized to fit the Steam Deck display without scrolling.

This repository currently provides the monitor UI and normalized velocity values. It does not yet transmit velocity commands to a robot controller.

## Stack

- Electron
- Vue 3 with the Composition API
- Vue Router using hash history
- Vite
- Bootstrap 5
- Lucide icons

## Requirements

- Linux or another Electron-supported desktop OS
- Node.js and npm
- A camera endpoint reachable from the device
- A gamepad exposed through the browser Gamepad API for hardware controls

The application was built primarily for SteamOS and Steam Deck Gaming Mode.

## Installation

Install the JavaScript dependencies:

```bash
npm install
```

## Development

Start Vite and Electron together:

```bash
npm run dev
```

Vite listens on `http://127.0.0.1:5173`. Electron waits for that URL and then loads it through `VITE_DEV_SERVER_URL`.

Other useful commands:

```bash
npm run build      # Build the renderer into dist/
npm run electron   # Launch Electron using the existing dist/ build
npm run start      # Build, then launch Electron
npm run preview    # Preview the production Vite build in a browser
```

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

The file is read and written by the Electron main process. Writes use a temporary file and rename operation to reduce the chance of a partially written settings file.

### Supported Camera Streams

The Home view renders the camera with an HTML `<img>` element. Directly supported sources are therefore:

- HTTP or HTTPS snapshots
- HTTP or HTTPS MJPEG streams

Although RTSP can be selected and saved, Chromium cannot display an `rtsp://` URL directly in an `<img>` element. RTSP cameras require an external relay or transcoder that exposes the stream as MJPEG, HLS, WebRTC, or another Chromium-compatible format. No relay is included in this repository yet.

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

A `0.12` dead zone is applied before values are normalized. Mouse and touch interaction use the same constrained axes. Sideways translation is intentionally not represented because the target is a differential-drive robot.

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
    │   └── index.js           Hash-based Home and Settings routes
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

## Current Limitations

- Velocity values are visualized but are not sent to a robot transport or middleware.
- RTSP playback needs an external browser-compatible relay.
- Camera authentication is not represented in the Settings form.
- Gamepad axis indices assume a conventional Steam Deck/gamepad mapping.
- There are no automated tests, lint rules, or packaged release artifacts yet.

## Contributing

Read [AGENTS.md](AGENTS.md) before making architectural changes. Preserve the secure Electron preload boundary and validate changes with:

```bash
npm run build
```

For camera or controller changes, also test interactively in Electron on the target Steam Deck when possible.
