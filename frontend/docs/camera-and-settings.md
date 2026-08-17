# Camera and Settings

The Settings drawer stores camera source, UDP destination, velocity limits, and keyboard preference in `settings.json` through Electron IPC. A successful load/save also sends the translated network configuration to FastAPI over WebSocket.

## Fields

- Camera stream type: HTTP or RTSP.
- Camera source IP, port, and optional subpath.
- UDP target host and port.
- Maximum linear Y and angular theta velocity, `0.1..100`.
- Built-in on-screen keyboard toggle.

The persisted contract remains:

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

Empty UDP host and port `0` disable backend UDP transmission. The form requires a camera host and port, but the underlying backend accepts an empty camera URL and keeps capture idle. The form does not expose HTTPS selection, credentials, query parameters, or fragments.

## Camera Path

The configured `cameraUrl` is sent to the backend as `camera_url`. FastAPI opens HTTP, HTTPS, or RTSP sources through OpenCV and exposes the result as MJPEG at `/stream`. `CameraFeed.vue` renders only this backend endpoint with `<img>`; it does not contact the source camera or Electron relay directly.

The renderer retries `/stream` two seconds after image errors. Camera state is connected only after the image begins loading successfully. Camera source URLs are not logged in full because they may contain credentials.

## Built-in Keyboard

When enabled, Settings fields are read-only and open the field-specific keyboard. Done commits and saves valid values; Cancel keeps the prior value. Native input and Enter-to-save return when disabled. Closing the drawer saves valid settings and leaves it open on invalid required fields. The app cannot disable Steam's global `Steam + X` keyboard overlay.
