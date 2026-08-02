# Camera and Settings

Open the floating gear button to use the right-side Settings drawer. The camera form currently exposes HTTP and RTSP sources as separate protocol, host, port, and subpath fields.

## Camera Fields

- **Stream type:** HTTP or RTSP
- **Source IP:** for example, `192.168.1.20`
- **Port:** for example, `8080`
- **Subpath:** optional, for example, `/video`

The form assembles these fields into `cameraUrl`. It does not preserve URL query parameters or fragments and does not currently expose HTTPS as a selectable protocol. Camera credentials may remain in a manually persisted URL, but the form does not provide credential fields.

## Robot and UDP Fields

- **Max Y-velocity:** linear cap for the left stick, `0.1` to `100`, default `10`
- **Max Theta-velocity:** angular cap for the right stick, `0.1` to `100`, default `10`
- **Target host:** optional robot UDP server IP address or hostname
- **Target port:** optional robot UDP server port, `1` to `65535`
- **On-screen keyboard:** field-specific Settings keyboard, enabled by default

Leaving either UDP destination field empty disables transmission. Existing settings files receive defaults for missing velocity limits, UDP destination values, and keyboard preference.

The persisted settings contract is:

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

The main process owns `settings.json` and writes through a temporary file followed by rename. The renderer shares values through `useSettings.js`.

## Built-in Keyboard

When enabled, Settings fields are read-only with `inputmode="none"`; selecting one opens a matching IP, hostname, integer, decimal, or path layout. **Done** commits the draft and saves valid Settings. **Cancel** leaves the original field unchanged. Native input is restored when the preference is disabled, and pressing **Enter** saves valid values. Closing the drawer saves valid values; invalid required fields keep it open.

The built-in keyboard does not disable Steam's global `Steam + X` keyboard overlay.

## Supported Streams

The Home view renders camera output with an HTML `<img>` element:

- HTTP or HTTPS snapshots and MJPEG streams load directly.
- RTSP sources are passed to the Electron main process, which runs bundled `ffmpeg` and exposes a tokenized MJPEG relay on `127.0.0.1`.

The RTSP relay uses RTSP/TCP, removes audio, scales within 1280x800, limits output to 15 fps, and uses CPU-based MJPEG encoding. Only one RTSP source runs at a time. Changing the source or quitting Electron stops the previous transcoder. Camera diagnostics use the `[camera]` prefix.
