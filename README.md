# Steam Deck Robot Monitor

A Steam Deck-oriented Electron application for viewing a robot camera and visualizing differential-drive inputs. The interface targets the Deck's 1280x800 display and combines an IP camera feed with live velocity values derived from hardware or on-screen joysticks.

> **Project status:** this repository implements the local monitor and input UI. It continuously sends Y and theta velocity commands to a configured UDP destination while the Home controller panel is active. It does not integrate directly with ROS or other robot middleware.

- [Steam Deck Robot Monitor](#steam-deck-robot-monitor)
  - [What It Does](#what-it-does)
  - [Stack](#stack)
  - [Requirements](#requirements)
  - [Quick Start](#quick-start)
  - [Commands](#commands)
  - [Steam Deck Launch](#steam-deck-launch)
  - [Camera Configuration](#camera-configuration)
    - [Supported Camera Streams](#supported-camera-streams)
  - [Controls](#controls)
  - [Architecture](#architecture)
    - [Electron Security Boundary](#electron-security-boundary)
    - [Renderer Data Flow](#renderer-data-flow)
  - [Current Limitations](#current-limitations)
  - [Contributing](#contributing)


## What It Does

- Displays HTTP snapshots, HTTP MJPEG streams, and RTSP camera streams.
- Transcodes RTSP to a loopback-only MJPEG stream with bundled `ffmpeg`.
- Reads Steam Deck or compatible controller input through the browser Gamepad API.
- Supports complete Steam Deck gamepad navigation for the sidebar, Settings form, and built-in keyboard.
- Maps the left stick vertical axis to linear Y velocity, capped at a configurable limit.
- Maps the right stick horizontal axis to angular theta velocity, capped at a configurable limit.
- Lets the maximum Y and theta velocities be set independently from the Settings page.
- Provides a built-in Settings keyboard that avoids duplicate Steam keyboard input.
- Supports pointer and touch dragging for both on-screen joystick controls.
- Stores the camera source, velocity limits, UDP destination, and keyboard preference in `settings.json` beside the launcher.
- Sends packed Y/theta velocity datagrams to the configured UDP destination every 100 ms.
- Runs as a frameless Electron application with Home and Settings routes plus an in-app Exit action.
- Uses a layout sized to fit the Steam Deck display without scrolling.

## Stack

- Electron
- Vue 3 with the Composition API
- Vue Router using hash history
- Vite
- Bootstrap 5
- Lucide icons
- ffmpeg-static

## Requirements

- Node.js `20.19+` or `22.12+`, and npm (required by Vite 8)
- Linux or another Electron-supported desktop operating system
- An HTTP snapshot, MJPEG, or RTSP endpoint reachable from the device
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

| Command            | Purpose                                          |
| ------------------ | ------------------------------------------------ |
| `npm run dev`      | Run Vite and Electron together for development   |
| `npm run build`    | Build the renderer into `dist/`                  |
| `npm run electron` | Launch Electron using the existing `dist/` build |
| `npm run start`    | Build the renderer, then launch Electron         |
| `npm run preview`  | Preview the production renderer in a browser     |

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

**Camera feed**

- **Stream type:** HTTP or RTSP
- **Source IP:** for example, `192.168.1.20`
- **Port:** for example, `8080`
- **Subpath:** optional, for example, `/video`

**Robot controls**

- **Max Y-velocity:** linear cap applied to the left stick, `0.1` to `100` (default `10`)
- **Max Theta-velocity:** angular cap applied to the right stick, `0.1` to `100` (default `10`)

**UDP destination**

- **Target host:** optional robot UDP server IP address or hostname
- **Target port:** optional robot UDP server port, `1` to `65535`

Leaving either UDP destination field empty disables transmission, preserving compatibility with settings files created before UDP support was added.

**Text input**

- **On-screen keyboard:** opens a field-specific built-in keyboard for Settings values (enabled by default)

The form assembles the camera fields into one URL and saves it alongside the velocity limits in `settings.json`:

```json
{
  "cameraUrl": "http://192.168.1.20:8080/video",
  "maxYVelocity": 10,
  "maxThetaVelocity": 10,
  "udpHost": "192.168.1.30",
  "udpPort": 5000,
  "useOnScreenKeyboard": true
}
```

The Electron main process owns the file but persists and returns the Settings IPC payload without applying defaults or normalization. The Vue views share the current values through `useSettings.js`, which supplies the default velocity limits and keyboard preference when stored keys are missing. Writes use a temporary file followed by rename to reduce the chance of a partially written settings file.

When the built-in keyboard is enabled, Settings text and number fields are read-only and use `inputmode="none"` so SteamOS has no editable field to target. Selecting a field opens a layout tailored to its value: IP address, integer, decimal, or stream path. **Done** commits the draft; **Cancel** leaves the original value unchanged. Disable the toggle and save Settings to restore normal native fields for a physical keyboard. The application cannot disable Steam's global keyboard overlay, which can still be opened with `Steam + X`.

The current Settings form exposes HTTP and RTSP and reconstructs the URL from protocol, host, port, and path. It does not preserve query parameters or fragments. If a camera URL needs HTTPS or query-string credentials/options, the current form must be extended before it can safely edit that URL.

### Supported Camera Streams

The Home view renders the camera with an HTML `<img>` element. HTTP sources are loaded directly:

- HTTP or HTTPS snapshots
- HTTP or HTTPS MJPEG streams

For an RTSP source, the renderer asks the Electron main process to start the bundled `ffmpeg` binary. It connects to the camera over RTSP/TCP, removes audio, scales video to fit within 1280x800, limits output to 15 fps, and encodes JPEG frames. The transcoder uses low-latency input flags, a fast bilinear scaler, two encoder threads, and JPEG quality `7` to reduce CPU use and end-to-end delay. A Node.js HTTP server bound to a random `127.0.0.1` port exposes those frames as a tokenized MJPEG stream for the existing `<img>` renderer.

Only one RTSP source runs at a time. Changing the camera URL stops the previous transcoder, and quitting Electron terminates it. The loopback relay accepts only its generated stream path and is not exposed to the local network. RTSP credentials can be present in the persisted URL, but the Settings form still does not provide credential fields or preserve arbitrary URL options.

Camera lifecycle and failure details are logged to the Electron renderer console with the `[camera]` prefix.

## Controls

### Interface navigation

| Input         | Action                                                                |
| ------------- | --------------------------------------------------------------------- |
| D-pad up/down | Select Home, Settings, or Exit in the sidebar                         |
| A             | Enter Settings, activate the focused control, or press a keyboard key |
| B             | Return from Settings to the sidebar, or cancel the built-in keyboard  |
| D-pad         | Move between Settings controls or built-in keyboard keys              |
| Left stick    | Move between built-in keyboard keys                                   |

The active route starts as the sidebar selection. Highlighting Exit does not change the current route; press **A** to quit. Pressing **A** on Settings moves focus into the first form control. D-pad directions then move spatially between form controls and keep the focused control visible as the Settings page scrolls. **A** opens an input, toggles a switch, presses a button, or advances a select option. Press **B** to return focus to the Settings sidebar item.

When the built-in keyboard is open, it takes control priority. D-pad and left-stick directions move between character and command keys, **A** presses the focused key, and **B** cancels without committing the draft. Closing the keyboard restores focus to its originating Settings field, and saving restores focus to the Save button, so subsequent D-pad input remains in Settings. Held directions repeat after a short delay. Mouse, touch, and physical keyboard input remain supported.

Left-stick navigation is limited to the keyboard modal. On Home, both analog sticks retain their robot-control behavior and do not navigate the interface. D-pad up/down can still switch between Home and Settings.

### Robot controls

| Input             | Robot value                                                      |
| ----------------- | ---------------------------------------------------------------- |
| Left stick up     | Positive Y velocity, up to the configured max Y-velocity         |
| Left stick down   | Negative Y velocity, down to the negative max Y-velocity         |
| Right stick left  | Negative theta velocity, down to the negative max theta-velocity |
| Right stick right | Positive theta velocity, up to the configured max theta-velocity |

Both limits default to `10` and are set independently on the Settings page. Stick output is normalized to `-1..+1` and then scaled by the matching limit.

The Gamepad API mapping currently reads:

- Left stick Y from `axes[1]`
- Right stick X from `axes[2]`

A `0.12` dead zone is applied to hardware gamepad values before they are normalized. Mouse and touch interaction use the same constrained axes but do not apply that dead zone. Sideways translation is intentionally not represented because the target is a differential-drive robot.

### UDP Packet Format

While the Home controller panel is mounted and a valid destination is configured, it calls `window.electronAPI.sendUdpMessage(host, port, 'ITS', [yVelocity, thetaVelocity])` every 100 ms. It sends one final zero-velocity command when the panel unmounts. The main process also sends a zero command to the last active destination before closing the UDP socket during application shutdown.

Each call sends one fixed-size, 11-byte UDP datagram. The header must be exactly three ASCII characters and velocity is `[yVelocity, thetaVelocity]`, encoded as two IEEE-754 32-bit floating-point values.

| Offset | Size | Encoding                          | Value          |
| ------ | ---- | --------------------------------- | -------------- |
| `0`    | 3    | ASCII bytes                       | Header         |
| `3`    | 4    | IEEE-754 `float32`, little-endian | Y velocity     |
| `7`    | 4    | IEEE-754 `float32`, little-endian | Theta velocity |

The receiver must use the same packed layout and little-endian byte order. The Electron main process validates the host and UDP port, while the UDP client validates the header, array shape, finite values, and `float32` range before transmission. Sends are not overlapped if a previous request is still pending.

## Architecture

```text
.
├── main.js                    Electron main process and settings IPC
├── preload.js                 Restricted renderer bridge
├── rtsp-transcoder.js         Loopback RTSP-to-MJPEG ffmpeg relay
├── udp-client.js              Packed Y/theta UDP datagram sender
├── launch.sh                  Steam Gaming Mode production launcher
├── settings.json              Persisted camera, controls, and UDP settings
├── index.html                 Renderer entry and Content Security Policy
├── vite.config.js             Vue/Vite production configuration
└── src
    ├── App.vue                Sidebar shell and application exit action
    ├── main.js                Vue, Bootstrap, and router initialization
    ├── styles.css             Steam Deck-oriented application styles
    ├── components
    │   ├── CameraFeed.vue     Camera state, <img> rendering, and status overlay
    │   ├── ControllerPanel.vue Gamepad polling, velocity math, and joystick layout
    │   ├── Joystick.vue       Reusable single-axis joystick with pointer/touch drag
    │   └── OnScreenKeyboard.vue Built-in Settings keyboard with tailored layouts
    ├── composables
    │   ├── useGamepad.js      Shared Gamepad API polling and prioritized UI actions
    │   └── useSettings.js     Shared reactive settings state and persistence interface
    ├── router
    │   └── index.js           Eager Home/Settings routes using hash history
    └── views
        ├── HomeView.vue       Composition shell rendering CameraFeed and ControllerPanel
        └── SettingsView.vue   Camera source and velocity-limit form and persistence
```

### Electron Security Boundary

The renderer runs with:

- `contextIsolation: true`
- `nodeIntegration: false`

`preload.js` exposes only five operations through `window.electronAPI`:

- `quitApp()`
- `loadSettings()`
- `saveSettings(settings)`
- `resolveCameraStream(cameraUrl)`
- `sendUdpMessage(host, port, header, velocity)`

Keep filesystem access, transcoder processes, and application lifecycle operations in the main process. Do not expose Node.js or Electron modules directly to Vue components.

### Renderer Data Flow

- `App.vue` owns the persistent shell and renders routed pages through `RouterView`.
- `HomeView.vue` and `SettingsView.vue` are route-level components, not manually toggled page components.
- `HomeView.vue` is a thin composition shell: `CameraFeed.vue` owns camera state and rendering, while `ControllerPanel.vue` owns gamepad polling, velocity math, and composes two `Joystick.vue` instances.
- `useGamepad.js` owns the single Gamepad API polling loop. Prioritized handlers route D-pad, left-stick, A, and B actions to the keyboard modal, Settings form, or sidebar while `ControllerPanel.vue` consumes the same reactive axes for robot velocity.
- `useSettings.js` owns one module-level reactive store shared across routes: the camera URL, per-axis velocity limits, and keyboard preference. `ControllerPanel.vue` reads the limits to scale its output.
- Vue code requests persistence through the preload bridge; only the Electron main process reads or writes `settings.json`.
- Hash history is intentional because production loads `dist/index.html` from `file://` without an HTTP server.

## Current Limitations

- UDP delivery is connectionless; the application cannot confirm that the robot received a datagram.
- RTSP transcoding is limited to one source at 15 fps and uses CPU-intensive MJPEG output.
- RTSP startup and authentication failures depend on `ffmpeg` diagnostics and are not surfaced as structured UI errors.
- Camera authentication is not represented in the Settings form.
- The Settings form does not expose HTTPS or preserve URL query parameters and fragments.
- Gamepad axis indices assume a conventional Steam Deck/gamepad mapping.
- The built-in keyboard prevents native Settings input while enabled, but cannot disable Steam's global `Steam + X` keyboard overlay.
- There are no automated tests, lint rules, installers, or packaged release artifacts yet.

## Contributing

Read [AGENTS.md](AGENTS.md) before making architectural changes. Preserve the secure Electron preload boundary and validate changes with:

```bash
npm run build
```

For camera or controller changes, also test interactively in Electron on the target Steam Deck when possible.
