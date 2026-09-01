# Camera and Settings

The Settings drawer stores camera source, UDP destination, velocity limits, and keyboard preference in `settings.json` through Electron IPC. A successful load/save also sends the translated network configuration to FastAPI over WebSocket.

## Fields

- One or more camera sources, each with a stream type (RTSP or direct WebSocket), source IP, port, and optional subpath.
- Optional RTSP username and password per source.
- Camera backend: `go2rtc` (default) or `aiortc`.
- UDP command target host/port and telemetry listening port.
- Maximum linear Y and angular theta velocity, `0.1..100`.
- Built-in on-screen keyboard toggle.
- Optional PTZ camera IP address for camera pan/tilt/zoom control.

The persisted contract remains:

```json
{
  "cameraSources": [
    {
      "type": "rtsp",
      "url": "rtsp://192.168.1.20:554/video",
      "username": "",
      "password": ""
    },
    {
      "type": "websocket",
      "url": "ws://192.168.1.21:8080",
      "username": "",
      "password": ""
    }
  ],
  "activeCameraIndex": 0,
  "cameraBackend": "go2rtc",
  "maxYVelocity": 10,
  "maxThetaVelocity": 10,
  "udpHost": "192.168.1.30",
  "udpPort": 5000,
  "udpListenPort": 8889,
  "useOnScreenKeyboard": true,
  "ptzIp": ""
}
```

Legacy files with top-level `cameraUrl`, `cameraType`, `cameraUsername`, and `cameraPassword` keys are read as a single source and rewritten into `cameraSources` on the next save.

`activeCameraIndex` selects which source is shown. Settings lists every source with a Show button, and pressing B (Circle) on the Home view cycles to the next source. Every source is connected at once and kept warm — the Home view mounts one `CameraFeed` per source and only shows the active one — so switching is instant with no reconnect. The backend receives every RTSP source in `camera_streams` and holds them all open. This trades steady CPU/GPU/bandwidth (one live decode per source) for an instant switch.

Empty UDP host and port `0` disable command transmission. `udpListenPort` remains required in `1..65535`, defaults to `8889`, and controls the backend telemetry bind port. The form requires a camera host and port, but the underlying backend accepts an empty `camera_streams` list and keeps capture idle. RTSP credentials are optional and persisted separately from each source `url`; the backend receives them as URL-encoded userinfo inside that source's `camera_streams[].url`. The form does not expose HTTPS selection, query parameters, or fragments.

## Camera Path

For RTSP, every source and `cameraBackend` are sent to the backend as `camera_streams` (a list of `{ id, url }`, `id` = `cam-<sourceIndex>`) and `camera_backend`. With the default `go2rtc` backend, FastAPI starts one local go2rtc process on demand, registers one named stream per source, and proxies receive-only WebRTC signaling through `POST /offer?src=<id>`. `aiortc` remains available as an explicit alternative. Each RTSP `CameraFeed.vue` negotiates one peer with FastAPI for its stream and renders the media track in `<video>`; holding that peer open is what keeps the source warm.

For WebSocket mode, Settings stores `ws://<IP>:<port>`. That `CameraFeed` connects directly to the camera, sends `PlayStream2`, ignores text status messages, and decodes binary H.264 messages with WebCodecs into a canvas. WebSocket sources are not sent in `camera_streams`. This path minimizes latency by avoiding a localhost camera relay and transcode.

Camera errors are surfaced by the WebRTC connection and retried by the existing camera lifecycle. Camera source URLs are not logged in full because they may contain credentials. Local development can override the go2rtc executable with `GO2RTC_BINARY`; Docker bundles a pinned, checksum-verified binary.

## PTZ Control

`ptzIp` stores the IP of a PTZ-capable camera (Hikvision ISAPI compatible). When set, the renderer forwards it to FastAPI inside the `config` message as `ptz_ip` together with optional `ptz_username` and `ptz_password`; the backend then drives the camera's ISAPI continuous-move endpoint on behalf of all connected UIs. The address is optional: an empty value disables PTZ, hides nothing in the UI, and simply keeps the backend from issuing camera HTTP requests. Controller bindings are documented in `controls.md`.

## Built-in Keyboard

When enabled, Settings fields are read-only and open the field-specific keyboard. Done commits and saves valid values; Cancel keeps the prior value. Native input and Enter-to-save return when disabled. Closing the drawer saves valid settings and leaves it open on invalid required fields. The app cannot disable Steam's global `Steam + X` keyboard overlay.
