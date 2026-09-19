# Camera and Settings

The Settings drawer stores camera source, UDP destination, velocity limits, and keyboard preference in `settings.json` through Electron IPC. A successful load/save also sends the translated network configuration to FastAPI over WebSocket.

## Fields

- One or more camera sources, each with a stream type (RTSP or direct WebSocket), source IP, port, and optional subpath. The type selects how the backend reaches the camera; the renderer receives both kinds identically.
- Optional RTSP username and password per source.
- Camera backend: `go2rtc` (default) or `aiortc`.
- RTSP transport: `tcp` (default) or `udp`, for how the live path carries RTP.
- UDP command target host/port and telemetry listening port.
- Maximum linear Y and angular theta velocity, `0.1..100`.
- Built-in on-screen keyboard toggle.
- Recording destination, chosen from the detected storage rather than typed.
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
  "rtspTransport": "tcp",
  "packetLimits": {
    "pwm": { "min": -100, "max": 100 },
    "steering": { "min": -100, "max": 100 }
  },
  "udpHost": "192.168.1.30",
  "udpPort": 5000,
  "udpListenPort": 8889,
  "useOnScreenKeyboard": true,
  "ptzIp": "",
  "ptzSpeedMultiplier": 1,
  "distancePerCount": 1,
  "wheelSeparation": 0.5,
  "recordingsDir": ""
}
```

Legacy files with top-level `cameraUrl`, `cameraType`, `cameraUsername`, and `cameraPassword` keys are read as a single source and rewritten into `cameraSources` on the next save.

`activeCameraIndex` selects which source is shown. Settings lists every source with a Show button, and pressing B (Circle) on the Home view cycles to the next source. Every source is connected at once and kept warm — the Home view mounts one `CameraFeed` per source and only shows the active one — so switching is instant with no reconnect. The backend receives every source in `camera_streams`, whatever its transport, and holds them all open. This trades steady CPU/GPU/bandwidth (one live decode per source) for an instant switch.

`packetLimits` bounds each field of the command packet, keyed by field name. The backend announces the settable fields (everything but padding) over the controls WebSocket on connect as `{ "type": "schema", "fields": [...] }`, and the Robot controls section renders one minimum/maximum row per field, so a schema change reaches the UI without a renderer edit. Values are clamped into the bounds `packets-schema.json` declares: the operator can narrow a field's range, never widen it past what the backend accepts. Files predating this setting are migrated from the old `maxYVelocity`/`maxThetaVelocity` caps, which became `{ "min": -cap, "max": cap }` for the fields with the `yVelocity` and `thetaVelocity` roles.

Empty UDP host and port `0` disable command transmission. `udpListenPort` remains required in `1..65535`, defaults to `8889`, and controls the backend telemetry bind port. The form requires a camera host and port, but the underlying backend accepts an empty `camera_streams` list and keeps capture idle. RTSP credentials are optional and persisted separately from each source `url`; the backend receives them as URL-encoded userinfo inside that source's `camera_streams[].url`. The form does not expose HTTPS selection, query parameters, or fragments.

## Camera Path

Every source, `cameraBackend`, and `rtspTransport` are sent to the backend as `camera_streams` (a list of `{ id, url }`, `id` = `cam-<sourceIndex>`), `camera_backend`, and `rtsp_transport`.

`rtspTransport` selects how the live path carries RTP for an RTSP source: `tcp` (default) interleaves it inside the RTSP connection, `udp` gives it its own datagrams. UDP has lower latency where the network allows it; a VPN or a filtered network drops it and the camera then connects and stays silent, which reads as a broken camera rather than a blocked port. It is sent as `rtsp_transport` and applies to the aiortc backend, which is where this process dials RTSP itself; go2rtc dials in its own child process and chooses for itself. Recording is always TCP: a dropped RTP packet is permanent corruption of that GOP in a stream copy, and a recording has no latency requirement to trade for it. Changing it re-dials every source while `aiortc` is the running backend, since a transport is chosen when a connection is opened. Under `go2rtc` nothing is torn down: that backend dials in its own child process and never reads the setting, so the value is stored for the next time aiortc runs and the live feeds carry on untouched.
 With the default `go2rtc` backend, FastAPI starts one local go2rtc process on demand, registers one named stream per source, and proxies receive-only WebRTC signaling through `POST /offer?src=<id>`. `aiortc` remains available as an explicit alternative. Each `CameraFeed.vue` negotiates one peer with FastAPI for its stream and renders the media track in `<video>`; holding that peer open is what keeps the source warm.

Both transports reach the renderer this way. For WebSocket mode, Settings still stores `ws://<IP>:<port>`, but the renderer no longer touches the camera: the backend opens the socket, sends `PlayStream2`, ignores text status messages, and re-serves the binary payloads at `GET /camera/<id>/stream` for its own camera backend to consume. This costs some latency compared with the previous renderer-direct WebCodecs path, and buys one camera transport instead of two — the renderer has no camera code, and anything the backend does across "all sources" works for every source kind. Nothing is re-encoded on the relay hop.

Camera errors are surfaced by the WebRTC connection and retried by the existing camera lifecycle. A WebSocket source that drops is redialed by the backend hub with backoff, independently of the renderer's own retry.

### When a feed stops moving

A camera that is unplugged, loses its link, or is dropped by a router usually
leaves its connection open and simply stops sending. No error is raised
anywhere: the peer stays connected and the renderer holds the last frame it
decoded, which reads exactly like a working camera pointed at something still.

So each `CameraFeed.vue` counts the frames its peer receives, once per second.
Five seconds without one and the feed blanks, shows "Connecting to camera...",
and offers again with `?restart=1` — which tells the backend to throw away the
connection it holds for that source and dial the camera again, rather than hand
out a second peer onto a feed that has already stopped. The count comes from the
receiver rather than from the `<video>` element, because every source but one is
off screen at any moment and a hidden feed is still a working feed. The aiortc
backend times the same silence on its own side, where it can drop the shared
connection a recording is also reading; go2rtc holds its connections in a child
process, so there the renderer's watchdog is the only thing that can see it.

A feed is "connected" only once video is actually arriving, not when the track
is negotiated. A track is a promise of video, not video.

A feed that keeps stalling says why under "Connecting to camera...": a source
that connects and then sends nothing at all is usually an RTSP transport the
network drops, which is a setting the operator can act on, and an unexplained
spinner is not.

### When a setting changes

Editing a camera's address or credentials, or changing `cameraBackend` or
`rtspTransport`, replaces live connections. The renderer does not act on saving
the form: it waits for `{"type":"camera","streams":[...]}`, which the backend
sends once the new configuration is in force, and reconnects exactly the feeds
it names. Reconnecting on save instead would race the config message and
negotiate against settings the backend has not applied yet. A feed that began
connecting after the change was sent is already negotiating against it and is
left alone. Camera source URLs are not logged in full because they may contain credentials. Local development can override the go2rtc executable with `GO2RTC_BINARY`; Docker bundles a pinned, checksum-verified binary.

## Recording

The HUD's record button captures every configured source at once, backend-side.
There is one control for the whole session rather than one per camera, because
the backend owns every source and records them uniformly.

The renderer only reflects state, never asserts it: it sends
`{ "type": "record", "action": "start" | "stop" }` and renders the
`{ "type": "recording", ... }` payload the backend pushes back. That payload is
not replayed on reconnect, and the HUD chip is deliberately *not* cleared when
the socket drops -- a recording outlives a two-second reconnect, and blanking
the chip would claim otherwise. It dims instead, until the backend re-states it.

The chip shows elapsed time and a count of any failed sources, so one dead
camera is visible without opening a log.

Files land in the Settings "Recordings folder" when one is set, otherwise in
`${SDRM_RECORDINGS_DIR:-$HOME/Videos/steamdeck-robot-monitor}` on the host. The
field takes an absolute path and is validated in the form before saving, so a
relative one is reported on the field rather than bouncing off the backend as a
rejected config message.

To record to an SD card, insert a formatted one and pick it from the list.
Settings reads `GET /storage/targets` on open and offers internal storage plus
every mounted card, each with its free space; Refresh re-reads it after inserting
one. Picking a card stores its folder, so the operator never sees a path — which
matters because SteamOS names the mount point after the card's label, or its UUID
when it has none, and Steam's own "Format SD Card" leaves it unlabeled.

The app creates its own `steamdeck-robot-monitor` folder on a chosen card, so a
blank card works as-is. With the card out, the record button reports it as
unavailable and the rest of the app is unaffected; pulling the card mid-recording
stops the session rather than writing into nowhere. A card that was selected and
is now absent stays in the list marked "Not connected", so the selection reads as
"that card is out" rather than silently reverting to internal storage.

Playback and stream history are not part of the app.

## PTZ Control

`ptzIp` stores the IP of a PTZ-capable camera (Hikvision ISAPI compatible). The Settings drawer's "Camera rotation (PTZ)" section has one field for it. It is sent to FastAPI inside the `config` message as `ptz_ip`; the camera credentials are hardcoded in the backend (`PTZ_USERNAME`/`PTZ_PASSWORD` in `PTZController.py`) rather than being configurable from the UI. The backend then drives the camera's ISAPI continuous-move, focus, and infrared-light endpoints on behalf of all connected UIs, trying digest auth first and falling back to basic on a `401`. The address is optional: an empty value disables PTZ and keeps the backend from issuing camera HTTP requests.

`distancePerCount` and `wheelSeparation` are the two measurements odometry needs: metres of travel per encoder count, and metres between the driven wheels. The telemetry packet carries raw counts, so the conversion is the renderer's -- `useOdometry` integrates the two reported wheel speeds over the time between packets into a pose, and Home shows the distance travelled in metres up to a kilometre and in kilometres above it. Only the heading uses `wheelSeparation`; the distance is the mean of the two wheels and does not involve it, so `0` leaves the heading untracked and the distance right. It lives beside the telemetry listen port in Settings, since it describes what arrives on that port. It defaults to `1`, which shows the count as it arrives -- only the operator knows their gearing and wheel size -- and `0` is allowed, reading as "do not show a distance".

`ptzSpeedMultiplier` is how fast pan and tilt run, as a step from 1 to 6 that the backend multiplies its base speed (15) by. Unlike every other setting here it is set from the Home view, not from the Settings drawer: the slider docked under the camera address applies each step as it is dragged and writes the file when the operator lets go. It is merged over the settings last read rather than replacing them, and the Settings form carries it through its own save, because `settings.json` is rewritten whole.

The renderer also only *sends* PTZ requests while `ptzIp`'s host matches the host of the camera currently on screen (`useSettings().ptzControlsActiveCamera`) — otherwise a held button would move a camera the operator is not watching. When they do not match, the on-screen focus and infrared-light buttons are hidden and D-pad/shoulder PTZ input is inert; `usePTZState` pushes a stop and clears its local state on the transition. The light is left as it is: it is latched in the camera, and switching feeds is not a reason to darken it.

Credentials are persisted in plain text in the Electron settings file, like the RTSP credentials. Controller bindings are documented in `controls.md`.

## Built-in Keyboard

When enabled, Settings fields are read-only and open the field-specific keyboard. Done commits and saves valid values; Cancel keeps the prior value. Native input and Enter-to-save return when disabled. Closing the drawer saves valid settings and leaves it open on invalid required fields. The app cannot disable Steam's global `Steam + X` keyboard overlay.
