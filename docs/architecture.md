# Architecture

## Repository Map

```text
.
├── main.js                    Electron main process and settings IPC
├── electron-components
│   ├── preload.js             Restricted renderer bridge
│   ├── rtsp-transcoder.js     Loopback RTSP-to-MJPEG relay
│   └── udp-client.js          Velocity sender and battery response receiver
├── udp-server.js              Development UDP diagnostic peer
├── launch.sh                  Steam Gaming Mode launcher
├── settings.json              Persisted runtime settings
├── index.html                 Renderer entry and Content Security Policy
├── vite.config.js             Vue/Vite production configuration
└── src
    ├── App.vue                Persistent command shell and Settings state
    ├── main.js                Vue and Bootstrap initialization
    ├── styles.scss            Ordered Sass entrypoint
    ├── styles                 Global style partials by responsibility
    ├── components              Camera, controller, joystick, keyboard, and drawer UI
    ├── composables              Shared settings, gamepad, and focus behavior
    ├── utils                    Spatial focus helpers
    └── views                    Home and Settings content
```

## Process Boundaries

Electron owns the application lifecycle, BrowserWindow, settings filesystem access, RTSP transcoder process, and 50 Hz UDP scheduler. Vue owns the camera-first UI, input interpretation, focus behavior, and Settings form. `useSettings.js` is the shared renderer source of truth for camera URL, velocity limits, UDP destination, and keyboard preference.

The camera and controller remain mounted while `SettingsShell.vue` opens the Settings view as a right-side drawer. This keeps camera and UDP lifecycles active while settings are edited.

## Electron Security

The renderer uses:

- `contextIsolation: true`
- `nodeIntegration: false`

`electron-components/preload.js` exposes only these narrow operations:

- `quitApp()`
- `loadSettings()`
- `saveSettings(settings)`
- `resolveCameraStream(cameraUrl)`
- `updateUdpVelocity(host, port, velocity)`
- `stopUdpVelocity()`
- `onUdpBatteryPercentage(callback)`

Filesystem access, child-process ownership, and UDP socket work remain in the main process. Vue code does not access Node.js or Electron modules directly.

## Renderer Data Flow

- `App.vue` owns the persistent shell and toggles `SettingsShell.vue`.
- `HomeView.vue` composes the camera, telemetry, and controller surfaces.
- `CameraFeed.vue` consumes settings and emits camera status for the telemetry HUD.
- `ControllerPanel.vue` consumes shared velocity limits and publishes the latest UDP destination and command state.
- `useGamepad.js` owns the single Gamepad API polling loop and prioritizes keyboard, Settings, and shell actions.
- `useSettingsGamepadNavigation.js` and `useOnScreenKeyboardNavigation.js` manage focus and activation for their respective surfaces.
