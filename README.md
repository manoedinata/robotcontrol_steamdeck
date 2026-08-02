# Steam Deck Robot Monitor

A Steam Deck-oriented Electron application for viewing a robot camera and visualizing differential-drive inputs. The interface targets the Deck's 1280x800 display as a camera-first command station, with a full-screen feed, compact telemetry, and floating controls.

> **Project status:** this repository implements the local monitor and input UI. It continuously sends Y and theta velocity commands to a configured UDP destination while the command shell is active. It does not integrate directly with ROS or other robot middleware.

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
    - [Interface navigation](#interface-navigation)
    - [Robot controls](#robot-controls)
    - [UDP Packet Format](#udp-packet-format)
  - [Architecture](#architecture)
    - [Electron Security Boundary](#electron-security-boundary)
    - [Renderer Data Flow](#renderer-data-flow)
  - [Current Limitations](#current-limitations)
  - [Contributing](#contributing)


## What It Does

- Displays HTTP snapshots, HTTP MJPEG streams, and RTSP camera streams.
- Transcodes RTSP to a loopback-only MJPEG stream with bundled `ffmpeg`.
- Reads Steam Deck or compatible controller input through the browser Gamepad API.
- Supports complete Steam Deck gamepad navigation for the floating shell actions, Settings drawer, and built-in keyboard.
- Maps the left stick vertical axis to linear Y velocity, capped at a configurable limit.
- Maps the right stick horizontal axis to angular theta velocity, capped at a configurable limit.
- Lets the maximum Y and theta velocities be set independently from a right-side Settings drawer.
- Provides a built-in Settings keyboard that avoids duplicate Steam keyboard input.
- Supports pointer and touch dragging for both on-screen joystick controls.
- Stores the camera source, velocity limits, UDP destination, and keyboard preference in `settings.json` beside the launcher.
- Sends packed Y/theta velocity datagrams to the configured UDP destination at 50 Hz (every 20 ms).
- Receives validated battery percentage replies from that UDP peer and displays them in the Home telemetry bar.
- Runs as a frameless Electron application with a persistent camera/control shell, sliding Settings drawer, and in-app Exit action.
- Uses a layout sized to fit the Steam Deck display without scrolling.

## Stack

- Electron
- Vue 3 with the Composition API
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

Install dependencies, start the local UDP diagnostic receiver and Vite, wait for Vite to become available, and launch Electron:

```bash
npm install
npm run dev
```

Vite listens on `http://127.0.0.1:5173`. Electron waits for that URL and then loads it through `VITE_DEV_SERVER_URL`. The diagnostic receiver listens on `0.0.0.0:41234`; set the Settings UDP destination to the Deck's address and port `41234` when you want to inspect packet values and timing locally.

## Commands

| Command            | Purpose                                           |
| ------------------ | ------------------------------------------------- |
| `npm run dev`      | Run the UDP receiver, Vite, and Electron together |
| `npm run build`    | Build the renderer into `dist/`                   |
| `npm run electron` | Launch Electron using the existing `dist/` build  |
| `npm run start`    | Build the renderer, then launch Electron          |
| `npm run preview`  | Preview the production renderer in a browser      |
| `npm run udp`      | Run only the UDP packet diagnostic receiver       |

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

Use the floating gear button to open the right-side **Settings** drawer and provide:

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

When the built-in keyboard is enabled, Settings text and number fields are read-only and use `inputmode="none"` so SteamOS has no editable field to target. Selecting a field opens a layout tailored to its value: IP address, integer, decimal, or stream path. **Done** commits the draft and immediately saves all valid Settings; **Cancel** leaves the original field value unchanged. With native editing enabled, pressing **Enter** saves all valid Settings. Closing the Settings drawer also saves before it closes; invalid required fields keep the drawer open for correction. Disable the toggle and save Settings to restore normal native fields for a physical keyboard. The application cannot disable Steam's global keyboard overlay, which can still be opened with `Steam + X`.

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

| Input      | Action                                                               |
| ---------- | -------------------------------------------------------------------- |
| D-pad      | Select a floating Settings or Exit action, or move within Settings   |
| A          | Open Settings, activate the focused control, or press a keyboard key |
| B          | Close the Settings drawer, or cancel the built-in keyboard           |
| D-pad      | Move between Settings controls or built-in keyboard keys             |
| Left stick | Move between built-in keyboard keys                                  |

The camera and robot controller remain mounted while Settings is open, so opening the drawer does not interrupt the feed or the main-process UDP scheduler. D-pad directions move spatially between drawer controls and keep the focused control visible while the drawer scrolls. **A** opens an input, toggles a switch, presses a button, or advances a select option. Press **B** to close the drawer and restore focus to the floating Settings button.

When the built-in keyboard is open, it takes control priority. D-pad and left-stick directions move between character and command keys, **A** presses the focused key, and **B** cancels without committing the draft. Closing the keyboard restores focus to its originating Settings field, and saving restores focus to the Save button, so subsequent D-pad input remains in Settings. Held directions repeat after a short delay. Mouse, touch, and physical keyboard input remain supported.

Left-stick navigation is limited to the keyboard modal. In the camera shell, both analog sticks retain their robot-control behavior and do not navigate the interface.

### Robot controls

| Input             | Robot value                                                      |
| ----------------- | ---------------------------------------------------------------- |
| Left stick up     | Positive Y velocity, up to the configured max Y-velocity         |
| Left stick down   | Negative Y velocity, down to the negative max Y-velocity         |
| Right stick left  | Negative theta velocity, down to the negative max theta-velocity |
| Right stick right | Positive theta velocity, up to the configured max theta-velocity |

Both limits default to `10` and are set independently in the Settings drawer. Stick output is normalized to `-1..+1` and then scaled by the matching limit.

The Gamepad API mapping currently reads:

- Left stick Y from `axes[1]`
- Right stick X from `axes[2]`

A `0.12` dead zone is applied to hardware gamepad values before they are normalized. Mouse and touch interaction use the same constrained axes but do not apply that dead zone. Sideways translation is intentionally not represented because the target is a differential-drive robot.

### UDP Packet Format

While the persistent controller panel is mounted and a valid destination is configured, it publishes the latest `[yVelocity, thetaVelocity]` state through `window.electronAPI.updateUdpVelocity(host, port, velocity)`. The Electron main process retains that latest command and sends it with header `ITS` at 50 Hz (every 20 ms). This keeps periodic scheduling and UDP socket work outside the renderer. A slow send is never overlapped; the next timer tick uses the most recently published state.

Leaving Home calls `stopUdpVelocity()` and clears the main-process timer. A full renderer navigation, renderer process exit, window close, or application quit also stops the scheduler so a stale command cannot continue after the renderer disappears. None of these paths sends a special final zero-velocity command; the receiving STM32 must use a UDP receive-timeout watchdog that stops the wheels when command datagrams cease.

Each transmission is one fixed-size, 11-byte UDP datagram. The header must be exactly three ASCII characters and velocity is `[yVelocity, thetaVelocity]`, encoded as two IEEE-754 32-bit floating-point values.

| Offset | Size | Encoding                          | Value          |
| ------ | ---- | --------------------------------- | -------------- |
| `0`    | 3    | ASCII bytes                       | Header         |
| `3`    | 4    | IEEE-754 `float32`, little-endian | Y velocity     |
| `7`    | 4    | IEEE-754 `float32`, little-endian | Theta velocity |

The receiver must use the same packed layout and little-endian byte order. `electron-components/udp-client.js` validates the destination, header, array shape, finite values, and `float32` range before transmission. Sends are not overlapped if a previous request is still pending.

The peer replies to the source address and ephemeral source port of a velocity command with a fixed-size, 7-byte battery datagram. The reply uses the same `ITS` header followed by a signed 32-bit little-endian integer. The Electron main process accepts only exact packets with a percentage from `0` through `100`, then forwards the validated value to the Home telemetry bar.

| Offset | Size | Encoding                      | Value              |
| ------ | ---- | ----------------------------- | ------------------ |
| `0`    | 3    | ASCII bytes                   | Header (`ITS`)     |
| `3`    | 4    | Signed `int32`, little-endian | Battery percentage |

`udp-server.js` is a development diagnostic peer, not part of the robot control path. It listens on UDP port `41234`, rejects packets that are not exactly 11 bytes or do not begin with `ITS`, decodes both velocities, and reports the monotonic interval between valid packets. For every valid command, it reads the current Linux battery capacity from `/sys/class/power_supply/BAT*/capacity` (cached for one second) and sends a battery reply. If no readable system battery exists, it logs one warning and does not send a fabricated percentage. `npm run dev` starts it automatically; only one process can bind that port at a time.

## Architecture

```text
.
├── main.js                    Electron main process and settings IPC
├── electron-components
│   ├── preload.js             Restricted renderer bridge
│   ├── rtsp-transcoder.js     Loopback RTSP-to-MJPEG ffmpeg relay
│   └── udp-client.js          Velocity sender and battery response receiver
├── udp-server.js              Development velocity/battery UDP peer
├── launch.sh                  Steam Gaming Mode production launcher
├── settings.json              Persisted camera, controls, and UDP settings
├── index.html                 Renderer entry and Content Security Policy
├── vite.config.js             Vue/Vite production configuration
└── src
    ├── App.vue                Persistent command shell, Settings and Exit actions
    ├── main.js                Vue and Bootstrap initialization
    ├── styles.css             Steam Deck-oriented application styles
    ├── components
    │   ├── CameraFeed.vue     Camera state, <img> rendering, and status events
    │   ├── ControllerPanel.vue Gamepad polling, velocity math, and joystick layout
    │   ├── Joystick.vue       Reusable single-axis joystick with pointer/touch drag
    │   ├── OnScreenKeyboard.vue Built-in Settings keyboard with tailored layouts
    │   └── SettingsShell.vue  Sliding right-side Settings drawer shell
    ├── composables
    │   ├── useGamepad.js      Shared Gamepad API polling and prioritized UI actions
    │   ├── useOnScreenKeyboardNavigation.js Keyboard-modal controller navigation
    │   ├── useSettingsGamepadNavigation.js Settings form controller navigation
    │   └── useSettings.js     Shared reactive settings state and persistence interface
    ├── utils
    │   └── spatialFocus.js    Shared geometry-based directional focus selection
    └── views
        ├── HomeView.vue       Full-screen camera, telemetry HUD, and controls
        └── SettingsView.vue   Drawer content for camera, UDP, and control settings
```

### Electron Security Boundary

The renderer runs with:

- `contextIsolation: true`
- `nodeIntegration: false`

`electron-components/preload.js` exposes only seven operations through `window.electronAPI`:

- `quitApp()`
- `loadSettings()`
- `saveSettings(settings)`
- `resolveCameraStream(cameraUrl)`
- `updateUdpVelocity(host, port, velocity)`
- `stopUdpVelocity()`
- `onUdpBatteryPercentage(callback)`

Keep filesystem access, transcoder processes, and application lifecycle operations in the main process. Do not expose Node.js or Electron modules directly to Vue components.

### Renderer Data Flow

- `App.vue` owns the persistent command shell and toggles `SettingsShell.vue` without unmounting the camera or controller.
- `HomeView.vue` composes the full-screen `CameraFeed.vue`, floating telemetry HUD, and `ControllerPanel.vue`. Camera connection state is emitted to the HUD; validated UDP battery replies update battery telemetry, and the displayed device IP falls back to a placeholder when no camera source is configured.
- `SettingsShell.vue` owns the modal right drawer while `SettingsView.vue` retains the settings form and persistence behavior.
- `useGamepad.js` owns the single Gamepad API polling loop. Prioritized handlers route D-pad, left-stick, A, and B actions to the keyboard modal, Settings drawer, or floating shell actions while `ControllerPanel.vue` consumes the same reactive axes for robot velocity.
- `useSettingsGamepadNavigation.js` and `useOnScreenKeyboardNavigation.js` own their respective focus and activation lifecycles. Both use `spatialFocus.js` for directional DOM focus selection, keeping navigation mechanics out of the Settings and keyboard components.
- `useSettings.js` owns one module-level reactive store shared across the shell and drawer: the camera URL, per-axis velocity limits, and keyboard preference. `ControllerPanel.vue` reads the limits to scale its output.
- `ControllerPanel.vue` publishes only changed destination/velocity state. The Electron main process owns the 20 ms UDP timer and repeatedly sends the latest state without overlapping socket sends.
- Vue code requests persistence through the preload bridge; only the Electron main process reads or writes `settings.json`.

## Current Limitations

- UDP delivery remains connectionless. A battery reply proves that the peer sent telemetry after a command, but it does not acknowledge a specific command or guarantee delivery.
- Battery telemetry remains `--` until a valid response arrives and has no stale-data timeout.
- Motion shutdown on lost or stopped transmission depends on the STM32 UDP receive-timeout watchdog; the application does not send a final stop datagram when Home unmounts or Electron quits.
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
