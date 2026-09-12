# Steam Deck Robot Monitor Backend

FastAPI owns robot transport and all camera transport for the Steam Deck UI. Vue sends configuration and control state over a local WebSocket; the backend sends commands at 50 Hz, receives battery telemetry on a separate UDP port, and converts every camera source to local WebRTC through the selected camera backend. Every configured source is kept warm at once so the UI can switch sources without a reconnect.

Two source kinds share one path. An RTSP url is dialed by the camera backend directly. A direct camera WebSocket url (`ws://`/`wss://`) is opened by `CameraWebSocketSource.py`, which sends the camera's `PlayStream2` handshake, holds exactly one connection per camera, and re-serves the bytestream at `GET /camera/<id>/stream` so go2rtc or aiortc can consume it like any other input. The renderer never connects to a camera.

## Endpoints

- `WS /ws/controls`: typed configuration and control messages.
- `POST /offer?src=<stream id>`: WebRTC SDP signaling for a receive-only video peer, for every source kind. `src` selects which configured stream the answer is for; it may be omitted only when exactly one stream is configured.
- `GET /camera/<stream id>/stream`: the raw bytestream of one source the backend holds a connection to — every WebSocket camera, and every RTSP camera while `camera_backend` is `aiortc`. This is an internal seam between the WebSocket hub and the camera backend, not a renderer endpoint. `?preroll=1` starts the stream at the camera's last keyframe rather than its next one; only recording asks for it, because the live path would open on video that is already seconds old.
- `GET /storage/targets`: the recording destinations Settings offers — internal storage plus each mounted removable filesystem, with capacity and writability.
- `GET /recordings`: every session in the folder the record button writes to, newest first, with each configured source and whether it produced video. Nothing is probed here.
- `GET /recordings/<session>`: one session with its files, their durations, codecs, and whether the renderer can play them.
- `GET /recordings/<session>/<file>/play`: that file remuxed to fragmented MP4, which Chromium can play. No Range support; `?t=<seconds>` re-opens at an offset, and `?transcode=1` is the explicit opt-in for a source the renderer cannot decode.
- `GET /recordings/<session>/<file>/download`: the Matroska file itself, with Range support, for copying off the Deck.
- `GET /recordings/<session>/<file>/thumbnail`: a JPEG poster frame. `?w=` is one of 160, 320, 640.
- `DELETE /recordings/<session>`: remove one session and everything in it. Refuses the session that is currently recording.
- `GET /health`: readiness probe used by the Docker entrypoint and external health checks.

Configuration message (RTSP credentials may be supplied as URL-encoded userinfo). `camera_streams` is the list of camera sources to keep connected, each with a renderer-assigned `id`:

```json
{"type":"config","config":{"udp_host":"127.0.0.1","udp_port":8888,"udp_listen_port":8889,"camera_streams":[{"id":"cam-0","url":"rtsp://user:password@camera/stream"}],"camera_backend":"go2rtc"}}
```

Control messages may update any subset of schema fields:

```json
{"type":"send","packet":{"vy":1.5,"vtheta":-0.25}}
```

Invalid messages receive `{"type":"error","message":"..."}` without closing the connection. Empty `udp_host` plus port `0` disables UDP. An empty or absent `camera_streams` list leaves camera capture idle. Stream ids must match `[A-Za-z0-9_-]{1,64}` and be unique; each url must be a valid `rtsp://`, `ws://`, or `wss://` URL with a hostname.

Valid robot telemetry is broadcast to all connected UIs:

```json
{"type":"receive","packet":{"battery_level":75}}
```

## Recording

One button records every configured source at once, one ffmpeg per source,
stream-copied (`-c copy`) into Matroska. Nothing is re-encoded.

```json
{"type":"record","action":"start"}
{"type":"record","action":"stop"}
```

Recording state is broadcast on every change, every two seconds while active,
and once to each UI on connect. It never contains a camera URL:

```json
{"type":"recording","active":true,"session_id":"20260909-123015",
 "directory":"/app/recordings/20260909-123015","free_bytes":43257610240,
 "seconds_remaining":2540,"stopped_reason":null,
 "sources":[{"id":"cam-0","kind":"rtsp","status":"recording","part":1,
             "file":"cam-0_001.mkv","bytes":18234312,"restarts":0,"error":null}]}
```

Per-source `status` is `idle`, `starting`, `recording`, `reconnecting`,
`failed`, or `stopped`; `stopped_reason` is `null`, `operator`, `low_disk`,
`folder_lost`, or `shutdown`. One source failing never stops the others.

Files go to `<recordings dir>/<YYYYmmdd-HHMMSS>/<stream id>_<part>.mkv`. The
directory is the config message's `recordings_dir` when the operator has set one
in Settings, otherwise the `RECORDINGS_DIR` environment variable
(`/app/recordings` in the container, bind-mounted from
`${SDRM_RECORDINGS_DIR:-$HOME/Videos/steamdeck-robot-monitor}`), otherwise a
`recordings/` directory beside the repo.

`recordings_dir` must be absolute, or be empty to mean "use the deployment
default". A relative path is rejected because it would resolve against whatever
directory the backend happened to be started from. Like the source list, a
change to it applies to the next session rather than moving a running one.

The operator does not type this path. Settings lists the cards found under
`/run/media` (overridable with `REMOVABLE_MEDIA_ROOT`) and sends the folder for
the one they pick, so a mount point named after a card's label or UUID never has
to be read, remembered, or typed.

**A folder is only ever created inside a directory that is genuinely a mount
point.** A freshly formatted card is empty, so the app makes its own
`steamdeck-robot-monitor` folder on it — but only once `os.path.ismount` confirms
the card is really there. Creating the mount point itself is what must never
happen: with the card absent that would invent a directory in the container's
ephemeral filesystem, or under `/run`, which is tmpfs, where recording fills RAM
until the Deck runs out. An absent card is therefore just a missing directory,
and recording refuses with "recordings folder is not available" while everything
else keeps running. Pulling the card mid-recording stops the session cleanly with
`stopped_reason: "folder_lost"`.

A candidate that is not a mount point is never offered: an unmounted card can
leave its directory behind, and recording into that would quietly fill the
internal drive under a name claiming otherwise.

Behavior worth knowing:

- The recordings directory is captured when recording starts, so editing it in
  Settings mid-session cannot move or split a running recording.
- The source list is frozen when recording starts. Switching cameras in the UI
  re-sends the whole config, so reacting to it would split every recording into
  parts each time the operator pressed B.
- A source that has written video is retried for the rest of the session with
  capped backoff, so a camera that reboots or drives out of range comes back on
  a new part. A source that never wrote a single byte is treated as
  misconfigured and gives up after three quick failures.
- A recording that stops growing for 15 s is restarted: ffmpeg will otherwise
  sit forever on an RTSP session that went quiet without erroring.
- Recording continues across a UI reconnect. Only an explicit stop, low disk, or
  backend shutdown ends it.
- It refuses to start below 2 GiB free and stops cleanly below 512 MiB, which
  trailers every file rather than letting several hit `ENOSPC` at once.
- A stream copy can only begin at a keyframe, and an IP camera commonly sends
  one every ten seconds. The hub keeps the payloads since the last one for every
  source it serves, and a recording is served them first (`?preroll=1`), so the
  file begins when the operator pressed record instead of at the camera's next
  keyframe. The buffered GOP arrives in a burst, so the seconds of video that
  precede the press sit at the head of the file as a brief blip before playback
  settles into real time.
- How a source is recorded follows who holds its connection, not what the camera
  is. A WebSocket camera is always read from the relay. An RTSP camera is too
  while `camera_backend` is `aiortc`, which holds one shared connection per
  source in this process — so recording costs no second camera session, and the
  file gets the preroll buffer. Under `go2rtc` the connection lives in a child
  process out of reach, so an RTSP recording dials the camera itself: a second
  session, and up to one keyframe interval missing from the front of the file.
  The per-source `kind` in the broadcast is the camera's own transport either
  way.

## Recordings Library

The same folder, read back. `GET /recordings` lists one entry per session
directory, newest first; opening one with `GET /recordings/<session>` adds the
per-file detail the page needs to play a clip.

The scope is deliberately the one folder the record button writes to, resolved
per request. `GET /storage/targets` already exists for choosing between cards.

### The manifest

Each session directory holds a `session.json` beside its files:

```json
{"version":1,"session_id":"20260909-213435","started_at":1757416475.0,
 "ended_at":1757416800.4,"status":"complete","stopped_reason":"operator",
 "sources":[
  {"id":"cam-0","kind":"rtsp","codec":null,"status":"stopped","restarts":1,
   "error":null,
   "parts":[{"file":"cam-0_001.mkv","bytes":18234312,"duration":121.5,
             "started_at":1757416475.2,"ended_at":1757416596.7}]},
  {"id":"cam-1","kind":"websocket","codec":"mjpeg","status":"failed",
   "restarts":0,"error":"no video was received","parts":[]}]}
```

It exists because the filesystem cannot answer the question the page asks. A
source that never received video has its empty file deleted, so "this camera
was recording and failed" and "this camera was not in this session" look
identical on disk. `cam-1` above is the case the manifest is for.

Like every recording payload it never contains a camera URL.

It is written at three moments and never on a timer: when the session starts,
before a single ffmpeg does; when a source finishes a part or gives up; and
when the session stops. A periodic rewrite would only add the byte count and
duration of the one part being written per source, both of which are
recoverable from `stat` and ffprobe — at the cost of rewriting a file on the
Deck's SD card every two seconds while that same card is taking N video
streams. Each write is a temp file plus `fsync` and `os.replace`, and a failure
costs a log line rather than the recording: the session degrades to the
filename scan below.

`status` on disk is only `recording` or `complete`. A session still reading
`recording` that the recorder is not running was written by a backend that was
killed, and reads back as `interrupted` — detected for free, with no extra
write.

A session that produced no video at all loses its folder on stop, manifest and
all, so a run where every source failed does not leave a dated directory per
attempt.

### When there is no manifest

A missing, torn, or unrecognised-version manifest is not an error. The session
is listed from its directory name and its filenames instead, with `manifest:
false` and `status: "scanned"`: source ids come from `<id>_<NNN>.mkv`, sizes and
modification times from `stat`, durations from ffprobe when the session is
opened, and everything else — camera kind, per-source status, restart counts —
reads as `null` or `unknown`. That one path covers recordings made before the
manifest existed and the last part of a session whose backend was killed, so
there is no separate crash-recovery case.

Bytes always come from disk, never from the manifest: a manifest written before
a backend was killed records what it knew at the time, and the file kept
growing.

### Playback

Matroska will not play in the renderer at all, so `/play` remuxes a part into
fragmented MP4 with `-c copy`. Fragmented is the only MP4 shape that can be
written to a pipe — a normal one seeks backwards to write its `moov` atom — and
the consequence is that the response carries no index and honours no Range.
Playback is forward-only; seeking is a new request with `?t=<seconds>`, which
lands on the nearest preceding keyframe and re-bases the output to zero, so the
UI adds the offset back itself.

An MJPEG recording is the awkward case: MP4 carries it and Chromium renders it
as nothing at all, which is the worst way to fail. The codec is probed first,
the detail endpoint reports `playable: false`, and `/play` answers `415` naming
the codec. `?transcode=1` is the deliberate opt-in that encodes it — this
device never pays for an encode by default, but review usually happens while
nothing is recording.

Two remuxes may run at once; a third is refused with `503` rather than left
hanging with its headers already sent. Every ffmpeg is registered for the
parent-death signal and is killed and reaped when the viewer navigates away.

### Thumbnails and caching

`/thumbnail` decodes one frame a second in (or halfway through a shorter clip)
and returns it as JPEG with an `ETag`. Both the posters and the ffprobe results
are cached **outside** the recordings folder — the posters under the system
temp directory, overridable with `RECORDING_THUMBNAIL_DIR`. That folder is
usually a card the operator browses in a file manager and which the recorder
itself refuses to write to when it is nearly full; turning a read into a write
there is the wrong trade for a file that costs one seek to rebuild. Every read
path leaves the card untouched, which is also what keeps the "never create a
path under `/run/media`" rule trivially true rather than conditionally true.

The probe cache is keyed by path, size, and mtime, so the part being written
right now is re-measured instead of serving a stale, short duration.

### Deleting

`DELETE /recordings/<session>` removes the directory and reports what it freed.
It refuses the running session with `409`, checked against the live recorder
rather than the manifest: a killed session's manifest also says `recording`, and
refusing those would block exactly the folders an operator most wants gone.

Session and file names from the URL are reduced to a safe component and then
checked to still resolve inside the root, the same idiom the writer uses. That
check is what refuses a symlink inside the recordings folder pointing out of
it, which matters more here than for the writer: this path is handed to
`rmtree`.

## PTZ Control

When `ptz_ip` is configured, the backend drives a PTZ camera's Hikvision ISAPI continuous-move endpoint (rotation and zoom) plus the FocusData focus endpoint on behalf of all connected UIs. The camera credentials are hardcoded (`PTZ_USERNAME`/`PTZ_PASSWORD` in `PTZController.py`) and are tried as digest auth first, falling back to basic auth on a `401` response. Setting `ptz_ip` to an empty string disables PTZ and stops all camera HTTP traffic.

The UI sends held PTZ requests over the controls WebSocket; `direction`, `zoom`, and `focus` are independent channels:

```json
{"type":"ptz","direction":"left","zoom":null,"focus":null}
{"type":"ptz","direction":null,"zoom":"zoom-in","focus":null}
{"type":"ptz","direction":null,"zoom":null,"focus":"focus-near"}
```

- `direction`: `"left"`, `"right"`, `"up"`, `"down"`, or `null`.
- `zoom`: `"zoom-in"`, `"zoom-out"`, or `null`.
- `focus`: `"focus-near"`, `"focus-far"`, or `null`.

A background loop sends exactly one rotation/zoom ISAPI command per tick (rotation takes priority over zoom, stop is sent when neither is active). Every rotation/zoom command is re-sent at 5 Hz — including stop, which is re-sent continuously while no request is active so the camera always halts even if the UI disconnects, crashes, or a stop packet is lost. Camera movements map to ISAPI pan/tilt values and zoom to the ISAPI zoom channel; both use fixed speeds.

Focus is edge-triggered instead of deadman-repeated: one `FocusData` command goes to `PUT /ISAPI/System/Video/inputs/channels/<n>/focus` when a focus value first appears, and one zero-speed `FocusData` stop is sent when it clears (including when the last UI disconnects). Rotation and zoom stop packets on the `PTZData` channel are unaffected.

## Binary UDP Schema

`../packets-schema.json` is the source of truth for the command header, endian, ordered fields, defaults, numeric types, and bounds. Supported field types are `int8`, `uint8`, `int16`, `uint16`, `int32`, `uint32`, `float32`, and `float64`.

The shipped command is exactly 11 bytes:

| Offset | Size | Encoding                | Value    |
| ------ | ---- | ----------------------- | -------- |
| `0`    | 3    | ASCII                   | `ITS`    |
| `3`    | 4    | little-endian `float32` | `vy`     |
| `7`    | 4    | little-endian `float32` | `vtheta` |

The telemetry receiver binds `0.0.0.0:8889` by default. Its packet is exactly 4 bytes: ASCII `ITS` followed by `battery_level` as a `uint8` percentage constrained to `0..100`. Packets with a wrong header, wrong length, or out-of-range value are discarded.

Adding a Vue input requires adding its initial value to `useControlState.js`, binding the component through `updatePacket()`, and adding the corresponding ordered field to `packets-schema.json`. The WebSocket dispatcher and UDP encoder require no field-specific handler or offset.

## Run

Requirements are Python 3.10+, FastAPI, Uvicorn, and OpenCV Python. Run one worker from `backend/` because runtime settings, connected-client count, packet state, and camera capture are process-local:

```bash
uvicorn server:app --host 127.0.0.1 --port 8000
```

The frontend defaults to `http://127.0.0.1:8000`. It can be built with a different local endpoint through `VITE_BACKEND_URL`, with the corresponding CSP allowlist updated in `frontend/index.html`.

## Docker

The project image bundles the backend with the frontend. Inside the container the backend is started from `/app/backend` and `PYTHONPATH=/app/backend` is set so modules resolve regardless of working directory. The Docker entrypoint waits for `/health` before launching Electron.

## Behavior

UDP transmission runs only while at least one controls WebSocket is connected and a complete destination is enabled. Disconnecting the final UI resets all controls to schema defaults. The app sends no special final stop datagram; the robot must enforce a UDP receive-timeout watchdog.

`camera_backend` defaults to `go2rtc`; `aiortc` remains available as an explicit alternative. With go2rtc, FastAPI starts one localhost-only go2rtc process on demand and registers every configured source as a named stream (the renderer's stream id), then proxies `/offer?src=<id>` SDP to go2rtc. A relayed WebSocket source is registered as an `exec:` ffmpeg source rather than a plain url, because the relay serves a bare bytestream with no container to identify it and the demuxer has to be named; the backend sniffs the camera's first payload to pick `h264` or `mjpeg`. A renderer that holds one peer per stream keeps every source connected, so switching is instant. The go2rtc API listens on `127.0.0.1:1984` and WebRTC media uses port `8555`. Set `GO2RTC_BINARY` to override the executable path during local development. A selected but unavailable go2rtc binary reports a camera error and does not silently fall back to aiortc.

With aiortc, each `/offer` creates an aiortc peer and RTSP media player for the requested stream. A config change closes only the peers whose source id or url actually changed; removing a stream or switching backend closes its peers, and FastAPI shutdown closes everything. The current deployment assumes the renderer and backend share the Steam Deck host; no STUN/TURN service is configured.

## Validation

Run the tests from `backend/`:

```bash
python -m unittest discover -s . -p "test_*.py"
```

To run the same tests inside the built container:

```bash
docker run --rm --network host -v "$PWD/..:/app" -w /app/backend \
	steamdeck-robot-monitor:latest python -m unittest discover -s . -p "test_*.py"
```

`../scripts/udp_server_simulation.py` decodes received commands. `../scripts/udp_telemetry_simulation.py 75` sends one schema-derived battery packet to the default telemetry port. Static validation does not require a camera or live UDP target.

## Limitations

- Runtime state is shared by all connected UIs and requires one Uvicorn worker.
- Telemetry currently contains only battery percentage; acknowledgement, sequence IDs, RTT, and loss are not implemented.
- WebRTC requires a reachable RTSP source. go2rtc is bundled in Docker and may use FFmpeg for codec conversion; local development requires `GO2RTC_BINARY` or a `go2rtc` executable on `PATH`.
- The current WebRTC ICE configuration is intended for local host/container playback only.
- RTSP credentials are supplied in each `camera_streams[].url` userinfo and should not be written to logs.
- A recording adds one RTSP connection per recorded RTSP source, on top of the one the live view already holds. WebSocket sources cost nothing extra: the recorder is another subscriber to the hub's single connection.
- Recorders are registered for a parent-death signal, so a backend killed outright (`SIGKILL`, an OOM kill, a crash) takes them down with it instead of leaving them holding a camera session and filling the disk. The signal is `SIGTERM`, so they still write their Matroska trailer and the recording stays a valid file. This is a Linux facility; on a platform without it the per-file time limit is the remaining backstop, and such a recording still plays but needs `ffmpeg -i in.mkv -c copy out.mkv` to rebuild its index.
- A direct camera WebSocket source adds one relay hop (camera socket to local HTTP to the camera backend), which costs latency the old renderer-direct WebCodecs path did not. Nothing is re-encoded on that hop.
- The relay address assumes the backend is reachable at `127.0.0.1:8000`; set `APP_BACKEND_PORT` when uvicorn runs on another port.
- Every configured RTSP source stays connected while a UI is open, so CPU, GPU, and bandwidth cost scales with the number of sources.
