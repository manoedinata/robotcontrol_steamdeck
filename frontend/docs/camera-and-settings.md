# Camera and Settings

The Settings drawer stores camera source, UDP destination, velocity limits, and keyboard preference in `settings.json` through Electron IPC. A successful load/save also sends the translated network configuration to FastAPI over WebSocket.

## Fields

- One or more camera sources, each with a stream type (RTSP or direct WebSocket), source IP, port, and optional subpath. The type selects how the backend reaches the camera; the renderer receives both kinds identically.
- Optional RTSP username and password per source.
- Camera backend: `go2rtc` (default) or `aiortc`.
- UDP command target host/port and telemetry listening port.
- Maximum linear Y and angular theta velocity, `0.1..100`.
- Built-in on-screen keyboard toggle.
- Optional PTZ camera IP address for camera pan/tilt/zoom/focus control. The camera credentials are hardcoded in the backend, not stored here.

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
  "packetLimits": {
    "pwm": { "min": -100, "max": 100 },
    "steering": { "min": -100, "max": 100 }
  },
  "udpHost": "192.168.1.30",
  "udpPort": 5000,
  "udpListenPort": 8889,
  "useOnScreenKeyboard": true,
  "ptzIp": ""
}
```

Legacy files with top-level `cameraUrl`, `cameraType`, `cameraUsername`, and `cameraPassword` keys are read as a single source and rewritten into `cameraSources` on the next save.

`activeCameraIndex` selects which source is shown. Settings lists every source with a Show button, and pressing B (Circle) on the Home view cycles to the next source. Every source is connected at once and kept warm — the Home view mounts one `CameraFeed` per source and only shows the active one — so switching is instant with no reconnect. The backend receives every source in `camera_streams`, whatever its transport, and holds them all open. This trades steady CPU/GPU/bandwidth (one live decode per source) for an instant switch.

`packetLimits` bounds each field of the command packet, keyed by field name. The backend announces the settable fields (everything but padding) over the controls WebSocket on connect as `{ "type": "schema", "fields": [...] }`, and the Robot controls section renders one minimum/maximum row per field, so a schema change reaches the UI without a renderer edit. Values are clamped into the bounds `packets-schema.json` declares: the operator can narrow a field's range, never widen it past what the backend accepts. Files predating this setting are migrated from the old `maxYVelocity`/`maxThetaVelocity` caps, which became `{ "min": -cap, "max": cap }` for the fields with the `yVelocity` and `thetaVelocity` roles.

Empty UDP host and port `0` disable command transmission. `udpListenPort` remains required in `1..65535`, defaults to `8889`, and controls the backend telemetry bind port. The form requires a camera host and port, but the underlying backend accepts an empty `camera_streams` list and keeps capture idle. RTSP credentials are optional and persisted separately from each source `url`; the backend receives them as URL-encoded userinfo inside that source's `camera_streams[].url`. The form does not expose HTTPS selection, query parameters, or fragments.

## Camera Path

Every source and `cameraBackend` are sent to the backend as `camera_streams` (a list of `{ id, url }`, `id` = `cam-<sourceIndex>`) and `camera_backend`. With the default `go2rtc` backend, FastAPI starts one local go2rtc process on demand, registers one named stream per source, and proxies receive-only WebRTC signaling through `POST /offer?src=<id>`. `aiortc` remains available as an explicit alternative. Each `CameraFeed.vue` negotiates one peer with FastAPI for its stream and renders the media track in `<video>`; holding that peer open is what keeps the source warm.

Both transports reach the renderer this way. For WebSocket mode, Settings still stores `ws://<IP>:<port>`, but the renderer no longer touches the camera: the backend opens the socket, sends `PlayStream2`, ignores text status messages, and re-serves the binary payloads at `GET /camera/<id>/stream` for its own camera backend to consume. This costs some latency compared with the previous renderer-direct WebCodecs path, and buys one camera transport instead of two — the renderer has no camera code, and anything the backend does across "all sources" works for every source kind. Nothing is re-encoded on the relay hop.

Camera errors are surfaced by the WebRTC connection and retried by the existing camera lifecycle. A WebSocket source that drops is redialed by the backend hub with backoff, independently of the renderer's own retry. Camera source URLs are not logged in full because they may contain credentials. Local development can override the go2rtc executable with `GO2RTC_BINARY`; Docker bundles a pinned, checksum-verified binary.

## PTZ Control

`ptzIp` stores the IP of a PTZ-capable camera (Hikvision ISAPI compatible). The Settings drawer's "Camera rotation (PTZ)" section has one field for it. It is sent to FastAPI inside the `config` message as `ptz_ip`; the camera credentials are hardcoded in the backend (`PTZ_USERNAME`/`PTZ_PASSWORD` in `PTZController.py`) rather than being configurable from the UI. The backend then drives the camera's ISAPI continuous-move and focus endpoints on behalf of all connected UIs, trying digest auth first and falling back to basic on a `401`. The address is optional: an empty value disables PTZ and keeps the backend from issuing camera HTTP requests.

The renderer also only *sends* PTZ requests while `ptzIp`'s host matches the host of the camera currently on screen (`useSettings().ptzControlsActiveCamera`) — otherwise a held button would move a camera the operator is not watching. When they do not match, the on-screen focus buttons are hidden and D-pad/shoulder PTZ input is inert; `usePTZState` pushes a stop and clears its local state on the transition.

Credentials are persisted in plain text in the Electron settings file, like the RTSP credentials. Controller bindings are documented in `controls.md`.

## Built-in Keyboard

When enabled, Settings fields are read-only and open the field-specific keyboard. Done commits and saves valid values; Cancel keeps the prior value. Native input and Enter-to-save return when disabled. Closing the drawer saves valid settings and leaves it open on invalid required fields. The app cannot disable Steam's global `Steam + X` keyboard overlay.
