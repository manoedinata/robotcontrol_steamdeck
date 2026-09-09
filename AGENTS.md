# Project Guidance

This repository contains a Steam Deck robot monitor: a FastAPI backend for camera and UDP transport, plus an Electron + Vue frontend.

## Scope Boundaries

- `backend/` owns control WebSocket, HTTP, UDP send/receive, binary packet encoding/decoding, camera backend selection, and all camera-to-WebRTC transport. Every configured source (`camera_streams`) is kept connected at once so the UI switches without a reconnect; `POST /offer?src=<id>` picks one. RTSP sources are dialed directly; direct camera WebSocket sources are pulled in by the backend and re-served over local HTTP (`GET /camera/<id>/stream`) so the same camera backend consumes them. go2rtc is managed as a localhost child process; aiortc is an explicit alternative.
- `frontend/` owns the user interface, input handling, and the renderer-side settings shape. It holds no camera transport of its own: every source arrives as WebRTC from the backend.
- `packaging/` owns Docker image build and the Steam launcher script, including the `RECORDINGS_DIR` mount recordings are written to.
- `packets-schema.json` is the single source of truth for the binary UDP command layout.

## Docker Packaging

- The container bundles both frontend and backend.
- Use host networking so the backend stays reachable at `127.0.0.1:8000` from the renderer.
- The runtime image sets `PYTHONPATH=/app/backend` so backend modules resolve regardless of cwd.
- `APP_SETTINGS_DIR` controls where Electron saves settings; the launcher bind-mounts a host directory there.
- The entrypoint starts uvicorn, waits for `GET /health`, then starts Electron, and tears both down together.
- Docker bundles pinned, checksum-verified go2rtc binaries for `amd64` and `arm64`; `GO2RTC_BINARY` is the local-development override.

## Documentation

Update the top-level `README.md`, backend/frontend `README.md`, `AGENTS.md` files, and focused `frontend/docs/` pages when adding endpoints, message types, packet fields, transport ownership, camera behavior, controls, settings shape, or packaging changes.

## Validation

- Run backend tests with `python -m unittest discover -s . -p "test_*.py"` from `backend/`.
- Run the same tests inside the built image with `docker run --rm --network host -v "$PWD:/app" -w /app/backend steamdeck-robot-monitor:latest python -m unittest discover -s . -p "test_*.py"`.
- Run frontend build with `npm run build`.
- Static validation must not require a live camera, backend server, browser, or UDP peer.
- Camera transport must keep the renderer on FastAPI `POST /offer?src=<id>` for every source kind; do not expose go2rtc directly to Electron, add automatic backend fallback, or give the renderer its own camera connection. The relay endpoint `GET /camera/<id>/stream` is an internal seam between the backend's WebSocket hub and its camera backend, not a renderer endpoint.
