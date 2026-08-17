# Backend Guidance

## Scope

This directory contains the FastAPI backend for robot control transport and camera monitoring. The repository may also contain a separate frontend.

## Structure

- `server.py`: application lifecycle, controls WebSocket, 50 Hz UDP sender, and shared RTSP-to-MJPEG stream.
- `settings.py`: mutable runtime network configuration.
- `utils.py`: frequency conversion and schema-derived default state.
- `../packets-schema.json`: authoritative UDP control packet schema.
- `../scripts/udp_server_simulation.py`: local UDP receiver.

## Runtime Contracts

- Keep UDP payload fields aligned with `packets-schema.json`.
- Configuration keys `ip`, `port`, and `rtsp_url` must not enter UDP payloads.
- Send UDP only while at least one controls WebSocket is connected.
- Reset controls after the final controls WebSocket disconnects.
- Keep blocking OpenCV capture off the asyncio event-loop thread.
- Share RTSP capture and JPEG encoding among HTTP stream subscribers.
- Run a single application worker because runtime state is process-local.

## Validation

After Python changes, compile `backend/` and `scripts/` from the repository root and check Pylance diagnostics. Do not require a live RTSP source for static validation. The user performs interactive server and browser validation.

## Documentation

Update `README.md` and this file when architecture, packet fields, endpoints, configuration, controls, or limitations change.