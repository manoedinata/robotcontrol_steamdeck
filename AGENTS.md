# AGENTS.md

## Repository Purpose

This repository is a Steam Deck-oriented Electron and Vue application for viewing a robot camera and visualizing differential-drive inputs. The Home view displays an IP camera stream and calculates linear Y and angular theta velocity from Steam Deck, gamepad, pointer, or touch input. The Settings view persists the camera source.

The target viewport is 1280x800. The primary runtime environment is SteamOS Gaming Mode.

While the command shell is active, the Electron main process sends packed Y/theta velocity datagrams at 50 Hz (every 20 ms) to the configured UDP destination and receives battery percentage replies on the same socket. It does not integrate directly with ROS or other robot middleware.

## Architecture

- `main.js`: Electron main process, BrowserWindow creation, IPC handlers, settings filesystem access, RTSP transcoder lifecycle, and the 50 Hz latest-command UDP scheduler.
- `electron-components/preload.js`: narrow context bridge exposed as `window.electronAPI`.
- `electron-components/http-camera-relay.js`: one-source HTTP/HTTPS camera passthrough relay bound to a tokenized loopback URL; forwards image bytes unchanged and counts complete JPEG frames.
- `electron-components/rtsp-transcoder.js`: one-source RTSP/TCP to MJPEG relay using bundled `ffmpeg`, bound to a tokenized loopback URL.
- `electron-components/udp-client.js`: validates and sends 11-byte velocity commands, receives validated 7-byte battery replies, and publishes battery updates in the main process.
- `udp-server.js`: development-only UDP peer that validates `ITS` commands, decodes both velocities, reports packet timing, and replies with the current Linux system battery percentage.
- `camera-server.js`: development-only mock IP camera that serves an MJPEG stream over HTTP at `http://localhost:8080/video`, using bundled `ffmpeg-static` to generate a synthetic test pattern by default or stream a v4l2 device via `CAMERA_DEVICE`. Device streams request the camera's native MJPEG and stream-copy it (no re-encode) to avoid transcode lag; `CAMERA_SIZE` and `CAMERA_FRAMERATE` set the capture geometry.
- `docs/`: focused user documentation for setup, camera/settings, controls, UDP protocol, architecture, and limitations.
- `src/App.vue`: persistent camera-first command shell, floating Settings/Exit actions, and Settings drawer state.
- `src/views/HomeView.vue`: full-screen camera composition with top-center connection/IP telemetry, a top-right UDP battery chip, and floating controller HUD.
- `src/components/CameraFeed.vue`: camera state, `<img>` rendering, and status events consumed by the Home telemetry HUD. Consumes `useSettings` directly.
- `src/components/SettingsShell.vue`: modal shell that slides in from the right and hosts `SettingsView` without unmounting Home.
- `src/components/ControllerPanel.vue`: Gamepad API polling, dead zone, per-axis velocity math, and the joystick/status layout.
- `src/components/Joystick.vue`: reusable single-axis joystick (`v-model` position, `axis` prop, pointer/touch drag with `drag-start`/`drag-end` events).
- `src/components/OnScreenKeyboard.vue`: modal Settings keyboard with IP, integer, decimal, and text layouts plus commit/cancel behavior.
- `src/views/SettingsView.vue`: drawer content for camera URL parsing/assembly, UDP destination, per-axis velocity limits, and the keyboard preference toggle.
- `src/composables/useSettings.js`: module-level reactive settings state (camera URL, velocity limits, and keyboard preference) shared between the shell and drawer.
- `src/composables/useGamepad.js`: shared Gamepad API polling, reactive axes/controller identity, held-direction repeat, and prioritized UI action dispatch.
- `src/composables/useSettingsGamepadNavigation.js`: Settings form focus movement, control activation, drawer close, and Save focus restoration.
- `src/composables/useOnScreenKeyboardNavigation.js`: keyboard-modal D-pad/stick navigation, A/B handling, Escape handling, and initial key focus.
- `src/utils/spatialFocus.js`: shared geometry-based directional focus selection used by Settings and the on-screen keyboard.
- `src/styles.scss`: Sass entrypoint that loads the global styles in cascade order.
- `src/styles/`: Sass partials for foundations, Settings and keyboard controls, the camera-first command shell, Settings drawer, and responsive rules. Keep partial ordering in `styles.scss` stable when selectors intentionally override earlier foundations.
- `settings.json`: local runtime configuration stored beside `launch.sh`.

### Runtime Modes

- `npm run dev`: runs the UDP diagnostic receiver, runs Vite on `127.0.0.1:5173`, waits for Vite, and launches Electron with `VITE_DEV_SERVER_URL`.
- `npm run build`: produces the renderer in `dist/`.
- `npm run electron`: launches Electron against an existing `dist/index.html`.
- `npm run start`: builds and then launches Electron.
- `npm run udp`: runs only `udp-server.js`, which binds `0.0.0.0:41234`.
- `npm run camera`: runs `camera-server.js`, a development-only mock IP camera that serves an MJPEG stream over HTTP at `http://localhost:8080/video`.
- `launch.sh`: launches Electron's native binary for Steam Gaming Mode and requires installed dependencies plus an existing build.

Browser preview is suitable for renderer layout checks, but settings persistence and the Exit action require the Electron preload bridge.

## Required Invariants

### Electron Security

Keep these BrowserWindow settings:

- `contextIsolation: true`
- `nodeIntegration: false`

Do not access Node.js, Electron, or the filesystem directly from Vue code. Add narrowly scoped IPC methods in `main.js` and expose only those methods through `electron-components/preload.js`. Validate renderer-provided IPC payloads at the main-process boundary unless a specific IPC contract, such as settings persistence, intentionally accepts renderer-owned structured data unchanged.

Keep application lifecycle and settings filesystem ownership in the main process. Do not broaden `window.electronAPI` with generic IPC, filesystem, or shell access.

The UDP preload contract is `updateUdpVelocity(host, port, velocity)`, `stopUdpVelocity()`, `onUdpBatteryPercentage(callback)`, and `onUdpMetrics(callback)`, where velocity is `[yVelocity, thetaVelocity]`. `ControllerPanel.vue` publishes only the latest reactive state. The main process retains the latest command and sends it every 20 ms with fixed header `ITS`; `electron-components/udp-client.js` validates the destination and serializes two finite IEEE-754 `float32` values before each send. Full renderer navigation, renderer termination, window close, and an explicit stop must clear the scheduler so a stale command cannot continue. Keep velocity packets packed with Y at byte offset `3`, theta at byte offset `7`, and little-endian byte order. Battery responses are exact 7-byte packets with `ITS` at offset `0` and a signed little-endian `int32` percentage at offset `3`; accept only values from `0` through `100` and expose only replies from the configured destination host and port to the renderer. Do not overlap sends. Close the UDP socket during application shutdown.

The UDP HUD displays an exact trailing-one-second valid reply count plus approximate RTT and packet loss. The main process pairs replies FIFO with unmatched sends, averages the latest 20 pair timings, and computes loss from independent reply and settled-send counts in a trailing five-second window with a 250 ms reply grace period. A valid reply within one second means connected. Reset samples on destination changes and transmission stop. Preserve the `~` marker in the HUD and documentation because exact per-command RTT/loss requires sequence IDs that the current 11-byte/7-byte protocol does not contain.

### Settings

The current persisted contract is:

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

`SettingsView.vue` presents protocol, IP, port, and subpath separately, then assembles one `cameraUrl`. It also exposes `maxYVelocity`, `maxThetaVelocity`, `udpHost`, and `udpPort`. `useSettings.js` keeps these values reactive across views. Preserve compatibility with existing `settings.json` files when changing this schema.

`maxYVelocity` and `maxThetaVelocity` are the per-axis velocity caps (default `10`). `useSettings.js` owns these defaults and applies them when stored keys are missing. The main process persists and returns the Settings IPC payload unchanged; validation constraints are expressed by the Settings form before it sends the payload.

`udpHost` defaults to an empty string and `udpPort` defaults to `0` for existing settings files, disabling transmission until a valid destination is saved. `ControllerPanel.vue` publishes updated destination and velocity state while mounted. The main process sends the latest state with header `ITS` every 20 ms. When Home unmounts, stop the main-process timer without issuing a special final zero-velocity command.

UDP destination fields are optional so users can save unrelated settings while transmission is disabled. The built-in keyboard's hostname layout must support letters, digits, dots, and hyphens. During application shutdown, close the UDP socket without sending a final stop datagram. The receiving STM32 owns motion fail-safe behavior and must stop the wheels when its UDP receive-timeout watchdog expires.

`useOnScreenKeyboard` defaults to `true` in `useSettings.js` for existing files where it is missing. When enabled, Settings inputs must remain read-only with `inputmode="none"` and open `OnScreenKeyboard.vue`; this prevents Steam keyboard events from modifying native fields. Disabling it restores native editing for physical keyboards. The app cannot and must not claim to disable Steam's global `Steam + X` overlay. Preserve Done-as-commit-and-save and Cancel-without-change semantics. Valid Settings autosave after OSK Done, after Enter during native input, and before the drawer closes. Invalid required fields must keep the drawer open rather than persisting malformed values.

The current form offers HTTP and RTSP only. Its parser does not preserve URL query parameters or fragments. Account for that limitation when changing URL handling; do not silently claim full arbitrary-URL editing or HTTPS form support.

Settings writes use a temporary file followed by `fs.rename`. Retain atomic-style writes for future settings.

Keep `useSettings.js` as the single shared renderer source of truth unless application state becomes complex enough to justify a dedicated store. Shell and drawer components should consume the composable rather than passing settings through `App.vue` props and events.

### Camera

`CameraFeed.vue` uses `<img>` for HTTP/HTTPS snapshots and MJPEG streams. HTTP/HTTPS sources pass through `electron-components/http-camera-relay.js`, which preserves the upstream bytes and content type while counting complete JPEG frames. Chromium cannot render `rtsp://` in `<img>`; RTSP sources are resolved through the Electron main process and `electron-components/rtsp-transcoder.js`. The RTSP relay runs bundled `ffmpeg` over RTSP/TCP, removes audio, scales within 1280x800 using `fast_bilinear`, limits output to 15 fps, uses two encoder threads, and serves JPEG quality `7` as MJPEG from a tokenized random port bound only to `127.0.0.1`. Low-latency input flags minimize buffering and probe delay.

Both relays report complete JPEG frames to the main process. The main process publishes the number received during the trailing one-second window every 500 ms through the narrow `onCameraFps(callback)` preload subscription. The camera HUD displays this as `FPS: <fps>` beneath the camera address. A snapshot produces one frame and then naturally returns to `0 FPS`; the value is stream throughput, not display refresh rate.

Keep camera relay and RTSP process ownership in the main process. Permit only HTTP, HTTPS, and RTSP camera protocols; do not expose process arguments, generic process spawning, or a network-accessible relay through the preload bridge. Preserve one active source, close upstream requests and terminate the child process on source changes and application quit, and avoid logging camera URLs because they may contain credentials. HLS or WebRTC output would require a corresponding browser renderer rather than the current `<img>` implementation.

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

### Gamepad Navigation

`useGamepad.js` is the single renderer polling owner for Steam Deck and compatible gamepads. `ControllerPanel.vue` consumes its reactive axes so Home robot control and interface navigation do not create competing Gamepad API loops.

- Standard button mapping is A at button `0`, B at button `1`, and D-pad up/down/left/right at buttons `12` through `15`.
- D-pad directions select Settings or Exit in the floating shell actions, and A activates the selected action. Opening Settings focuses its first form control.
- While a Settings control has focus, D-pad directions move spatially between controls, A activates the focused control, and B closes the drawer and returns focus to the floating Settings action.
- While `OnScreenKeyboard.vue` is open, it has the highest input priority. D-pad and left-stick axes `0`/`1` select keys, A activates a key, and B cancels the draft.
- Closing the on-screen keyboard restores focus to its originating Settings control. Completing a Settings save restores focus to the Save button so D-pad input remains owned by Settings.
- Held directional input repeats only after an initial delay. Left-stick navigation uses a `0.55` threshold.
- Left-stick navigation must remain keyboard-modal-only. On Home, the left and right sticks retain their robot velocity behavior; interface navigation must not consume or suppress those axes.
- Maintain visible focus styles and scroll focused Settings controls into view.

### UI Layout

The application must fit the Steam Deck's 1280x800 viewport without page scrolling. Preserve:

- A full-screen camera feed as the primary surface.
- Floating top-center telemetry for connection state and camera/device IP.
- A separate floating top-right UDP battery level chip.
- Stable translucent joystick controls anchored to the lower corners.
- Minimal floating Settings and Exit icon actions stacked vertically on the right edge, with Exit above Settings.
- A modal Settings shell that slides in from the right and scrolls independently.
- Stable joystick dimensions and puck travel.
- Touch- and controller-focusable built-in keyboard keys with stable dimensions.
- One-row camera settings at the target viewport.
- Usable responsive behavior down to the Electron window minimum of 960x640. The current Settings row needs explicit verification at that minimum and may require responsive work.

Use existing Bootstrap controls, Lucide icons, and local CSS conventions. Avoid adding a second design system.

### Shell And Views

- Keep the full-screen camera and controller mounted as the persistent operational surface.
- Treat the Settings drawer as a shell component, not a route or replacement view; opening it must not stop camera or UDP control lifecycles.
- Keep Settings form content in `src/views/SettingsView.vue` and application chrome/state in `src/App.vue` and `src/components/SettingsShell.vue`.

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

`udp-server.js` is only a packet-format, timing, and battery-response diagnostic. It is started by `npm run dev`, accepts only exact 11-byte `ITS` commands, excludes malformed packets from interval calculations, and replies to each valid sender with a 7-byte `ITS` plus `int32` battery packet. It reads Linux battery capacity from `/sys/class/power_supply/BAT*/capacity`, caches it for one second, and sends no reply if no battery is readable. It does not emulate the STM32 watchdog or participate in production `npm run start`/`launch.sh` execution.

## Change Guidance

- Read the current file contents before editing; this repository may contain uncommitted user changes.
- Keep changes scoped and preserve the existing Vue Composition API style.
- Prefer shared reactive state in `useSettings.js` over prop drilling between shell and drawer components.
- Preserve the camera settings JSON contract unless a compatible migration is included.
- Do not commit generated `dist/` or `node_modules/` content.
- Do not overwrite a user's local camera URL in `settings.json` while performing unrelated work.
- Run `npm run build` after renderer, Electron, configuration, or documentation-adjacent command changes.
- Keep changes focused; do not introduce a state library, second UI framework, or stream player without a concrete requirement.
- On any major change (new feature, architecture shift, settings-schema change, control/behavior change), update both `README.md` and this `AGENTS.md` in the same change so the docs stay in sync with the code.

## Known Gaps

- UDP remains connectionless. Battery telemetry demonstrates that the configured peer responded but does not acknowledge a specific velocity command or guarantee delivery.
- UDP RX rate is exact for valid configured-peer replies, but RTT and loss are FIFO estimates because the protocol has no sequence IDs. Battery percentage remains at its last valid value after replies stop; UDP health separately becomes stale after one second.
- The application intentionally sends no final zero-velocity datagram when Home unmounts or Electron quits; wheel stopping on communication loss depends on the STM32 UDP receive-timeout watchdog.
- RTSP is supported through the main-process MJPEG relay; Chromium still cannot render `rtsp://` directly.
- Camera credentials are not represented by the Settings form, although manually persisted URL credentials can be passed to `ffmpeg`.
- RTSP transcoding supports one source, outputs MJPEG at 15 fps, and may be CPU intensive.
- RTSP connection and authentication errors are currently available through `ffmpeg` diagnostics rather than structured renderer status.
- The Settings form does not expose HTTPS or preserve URL query parameters and fragments.
- The built-in keyboard cannot disable Steam's global keyboard overlay; it only protects the Settings inputs from native keyboard insertion while enabled.
- The one-row Settings layout needs verification at the 960x640 minimum window size.
- Automated tests, linting, installers, packaging, and release automation are not configured.
