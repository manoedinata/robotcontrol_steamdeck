# Steam Deck Robot Monitor

A Steam Deck-oriented Electron and Vue command station for viewing a robot camera and controlling a differential-drive robot. The camera feed remains the primary surface while compact telemetry, on-screen joysticks, gamepad navigation, and a right-side Settings drawer stay available at the Deck's 1280x800 target viewport.

The application supports HTTP snapshots/MJPEG streams and RTSP sources, translating RTSP through a loopback-only `ffmpeg` relay. It maps the left stick to linear Y velocity and the right stick to angular theta velocity, then sends the latest command to a configured UDP destination at 50 Hz. It does not integrate directly with ROS or other robot middleware.

## Features

- Camera-first, frameless Electron UI for SteamOS Gaming Mode
- HTTP, MJPEG, and RTSP camera support
- Configurable Y/theta velocity limits with pointer, touch, and gamepad controls
- Full Steam Deck navigation for the shell, Settings drawer, and built-in keyboard
- Validated UDP velocity packets and battery telemetry
- Persistent camera, control, UDP, and keyboard settings

## Quick Start

```bash
npm install
npm run dev
```

`npm run dev` starts the UDP diagnostic receiver, Vite, and Electron. Build and launch the production renderer with `npm run start`, or use `./launch.sh` for Steam Gaming Mode after `npm run build`.

To test the camera pipeline without hardware, `npm run camera` serves a synthetic MJPEG stream at `http://localhost:8080/video`. See [Setup and launch](docs/setup.md#mock-camera-server) for details.

## Documentation

- [Setup and launch](docs/setup.md)
- [Camera and Settings](docs/camera-and-settings.md)
- [Controls](docs/controls.md)
- [UDP control protocol and packets](docs/udp.md)
- [Architecture and security](docs/architecture.md)
- [Current limitations](docs/limitations.md)
- [Contributor and repository guidance](AGENTS.md)

## Stack

Electron, Vue 3, Vite, Bootstrap 5, Lucide icons, and `ffmpeg-static`.

There are currently no automated test or lint scripts. Run `npm run build` as the minimum validation after changes. For camera or controller changes, verify the complete workflow in Electron on the target Steam Deck when possible.
