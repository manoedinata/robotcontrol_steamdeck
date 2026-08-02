# Setup and Launch

## Requirements

- Node.js `20.19+` or `22.12+` and npm (required by Vite 8)
- Linux or another Electron-supported desktop operating system
- A reachable HTTP, MJPEG, or RTSP camera endpoint
- A browser-visible gamepad for hardware input; pointer and touch controls are also supported

The application is primarily intended for SteamOS and Steam Deck Gaming Mode at 1280x800.

## Development

Install dependencies and run the UDP diagnostic receiver, Vite, and Electron together:

```bash
npm install
npm run dev
```

Vite listens on `http://127.0.0.1:5173`. The diagnostic UDP receiver listens on `0.0.0.0:41234`.

## Commands

| Command            | Purpose                                           |
| ------------------ | ------------------------------------------------- |
| `npm run dev`      | Run the UDP receiver, Vite, and Electron together |
| `npm run build`    | Build the renderer into `dist/`                   |
| `npm run electron` | Launch Electron using the existing `dist/` build  |
| `npm run start`    | Build the renderer, then launch Electron          |
| `npm run preview`  | Preview the production renderer in a browser      |
| `npm run udp`      | Run only the UDP packet diagnostic receiver       |

The browser preview is useful for layout work. Settings persistence and quitting require Electron's preload bridge. There is currently no automated test or lint script; `npm run build` is the minimum validation command.

## Steam Deck Gaming Mode

`launch.sh` launches Electron's native binary directly, which avoids the Node-based Electron CLI shim in Gaming Mode:

```bash
npm install
npm run build
./launch.sh
```

The script resolves paths relative to the repository, checks for Electron and `dist/index.html`, clears `LD_PRELOAD`, and starts `node_modules/electron/dist/electron`.

To add it as a non-Steam game:

```bash
chmod +x launch.sh
```

Add `launch.sh` to Steam as the non-Steam game's launch target.
