# Camera and Settings

The Settings drawer stores camera source, UDP destination, velocity limits, and keyboard preference in `settings.json` through Electron IPC. A successful load/save also sends the translated network configuration to FastAPI over WebSocket.

## Fields

- Camera stream type: RTSP.
- Camera source IP, port, and optional subpath.
- Optional RTSP username and password.
- UDP command target host/port and telemetry listening port.
- Maximum linear Y and angular theta velocity, `0.1..100`.
- Built-in on-screen keyboard toggle.

The persisted contract remains:

```json
{
  "cameraUrl": "http://192.168.1.20:8080/video",
  "cameraUsername": "",
  "cameraPassword": "",
  "maxYVelocity": 10,
  "maxThetaVelocity": 10,
  "udpHost": "192.168.1.30",
  "udpPort": 5000,
  "udpListenPort": 8889,
  "useOnScreenKeyboard": true
}
```

Empty UDP host and port `0` disable command transmission. `udpListenPort` remains required in `1..65535`, defaults to `8889`, and controls the backend telemetry bind port. The form requires a camera host and port, but the underlying backend accepts an empty camera URL and keeps capture idle. RTSP credentials are optional and persisted separately from `cameraUrl`; the backend receives them as URL-encoded userinfo in its transient `camera_url` configuration. The form does not expose HTTPS selection, query parameters, or fragments.

## Camera Path

The configured `cameraUrl` is sent to the backend as `camera_url`. FastAPI opens the RTSP source through aiortc/PyAV and exposes a receive-only WebRTC peer through `POST /offer`. `CameraFeed.vue` negotiates only with this backend endpoint and renders the returned media track in `<video>`; it does not contact the source camera or Electron relay directly.

The renderer retries `/stream` two seconds after image errors. Camera state is connected only after the image begins loading successfully. Camera source URLs are not logged in full because they may contain credentials.

## Built-in Keyboard

When enabled, Settings fields are read-only and open the field-specific keyboard. Done commits and saves valid values; Cancel keeps the prior value. Native input and Enter-to-save return when disabled. Closing the drawer saves valid settings and leaves it open on invalid required fields. The app cannot disable Steam's global `Steam + X` keyboard overlay.
