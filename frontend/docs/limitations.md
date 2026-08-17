# Limitations

- The FastAPI backend must be started separately; Electron does not supervise it.
- Backend runtime state is process-local and requires a single Uvicorn worker.
- All connected UIs share one UDP destination, camera source, and control packet.
- UDP is send-only. Robot telemetry, command acknowledgement, battery, RX rate, RTT, and loss are not implemented.
- UDP is connectionless; robot motion shutdown depends on its receive-timeout watchdog.
- Camera output is re-encoded to MJPEG through OpenCV, which uses CPU and more bandwidth than compressed H.264/H.265 forwarding.
- OpenCV camera protocol/codec support and buffering behavior vary by platform.
- Camera credentials are not represented by the Settings form.
- The Settings form does not expose HTTPS or preserve URL queries and fragments.
- `VITE_BACKEND_URL` is build-time configuration, and non-default endpoints require a matching Content Security Policy update.
- Gamepad axis indices assume a conventional Steam Deck/gamepad mapping.
- The built-in keyboard cannot disable Steam's global `Steam + X` overlay.
- There are no frontend automated tests or lint rules. Backend codec coverage is focused and does not exercise live WebSocket, UDP, or camera behavior.
- Installers, packaged releases, and backend service management are not configured.
