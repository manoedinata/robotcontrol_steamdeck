# Limitations

- UDP delivery is connectionless. A battery reply does not acknowledge a specific command or guarantee delivery.
- Battery telemetry remains at its last valid value after replies stop; there is no stale-data timeout.
- Motion shutdown after transmission stops depends on the STM32 UDP receive-timeout watchdog. The application sends no final zero-velocity packet on Home unmount or Electron quit.
- RTSP is limited to one source at 15 fps and uses CPU-intensive MJPEG output.
- RTSP startup and authentication failures are available through `ffmpeg` diagnostics rather than structured renderer errors.
- Camera credentials are not represented by the Settings form.
- The Settings form does not expose HTTPS or preserve URL query parameters and fragments.
- Gamepad axis indices assume a conventional Steam Deck/gamepad mapping.
- The built-in keyboard protects Settings fields from native input but cannot disable Steam's global `Steam + X` overlay.
- There are no automated tests, lint rules, installers, or packaged release artifacts.
