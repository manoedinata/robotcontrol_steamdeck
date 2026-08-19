# Camera and Settings

The Settings drawer stores camera source, UDP destination, velocity limits, and keyboard preference in `settings.json` through Electron IPC. A successful load/save also sends the translated network configuration to FastAPI over WebSocket.

## Fields

- Camera stream type: RTSP or direct WebSocket.
- Camera source IP, port, and optional subpath.
- Optional RTSP username and password.
- Camera backend: `go2rtc` (default) or `aiortc`.
- UDP command target host/port and telemetry listening port.
- Maximum linear Y and angular theta velocity, `0.1..100`.
- Built-in on-screen keyboard toggle.

The persisted contract remains:

```json
{
  "cameraType": "rtsp",
  "cameraUrl": "rtsp://192.168.1.20:554/video",
  "cameraUsername": "",
  "cameraPassword": "",
  "cameraBackend": "go2rtc",
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

For RTSP, the configured `cameraUrl` and `cameraBackend` are sent to the backend as `camera_url` and `camera_backend`. With the default `go2rtc` backend, FastAPI starts a local go2rtc process on demand, configures the named RTSP stream, and proxies receive-only WebRTC signaling through `POST /offer`. `aiortc` remains available as an explicit alternative. `CameraFeed.vue` negotiates only with FastAPI and renders the returned media track in `<video>`.

For WebSocket mode, Settings stores `ws://<IP>:<port>`. The renderer connects directly to the camera, sends `PlayStream2`, ignores text status messages, and decodes binary H.264 messages with WebCodecs into a canvas. The backend receives an empty `camera_url`, which keeps the RTSP transport idle. This path minimizes latency by avoiding a localhost camera relay and transcode.

Camera errors are surfaced by the WebRTC connection and retried by the existing camera lifecycle. Camera source URLs are not logged in full because they may contain credentials. Local development can override the go2rtc executable with `GO2RTC_BINARY`; Docker bundles a pinned, checksum-verified binary.

## Built-in Keyboard

When enabled, Settings fields are read-only and open the field-specific keyboard. Done commits and saves valid values; Cancel keeps the prior value. Native input and Enter-to-save return when disabled. Closing the drawer saves valid settings and leaves it open on invalid required fields. The app cannot disable Steam's global `Steam + X` keyboard overlay.
