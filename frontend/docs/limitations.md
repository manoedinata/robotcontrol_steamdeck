# Limitations

- Local development: the FastAPI backend must be started separately; Electron does not supervise it. The Docker path bundles and supervises both processes inside one container.
- Backend runtime state is process-local and requires a single Uvicorn worker.
- All connected UIs share one UDP destination, camera source, and control packet.
- Robot telemetry currently exposes only battery percentage. The HUD also shows ICMP host latency to the configured UDP destination, but command acknowledgement, sequence IDs, exact UDP command RTT, RX rate, and loss are not implemented.
- UDP is connectionless; robot motion shutdown depends on its receive-timeout watchdog.
- Camera playback requires a reachable RTSP source and FFmpeg/PyAV support through `aiortc`.
- WebRTC ICE is currently configured for local Steam Deck/container playback; remote NAT traversal is not provided.
- RTSP credentials are supported, but are stored in the local Electron settings file as plain text; protect access to that file.
- The Settings form supports RTSP only and does not preserve URL queries or fragments.
- `VITE_BACKEND_URL` is build-time configuration, and non-default endpoints require a matching Content Security Policy update.
- Gamepad axis indices assume a conventional Steam Deck/gamepad mapping.
- The built-in keyboard cannot disable Steam's global `Steam + X` overlay.
- There are no frontend automated tests or lint rules. Backend codec coverage is focused and does not exercise live WebSocket, UDP, or camera behavior.
- Installers and native packaged releases are not configured. Docker image packaging and a Steam launcher script are provided, but host-side service management is not.
