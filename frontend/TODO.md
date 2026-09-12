# TODO

## Transport

- [x] Move UDP sending and camera capture from Electron to FastAPI.
- [x] Connect Vue to FastAPI through typed WebSocket messages.
- [x] Encode UDP commands from the shared ordered binary schema.
- [x] Add a separate backend UDP receive path and typed robot battery telemetry messages on a configurable listening port.
- [ ] Add sequence IDs if command acknowledgement, exact RTT, or loss measurements are required.

## Product

- [x] Username & password support for RTSP connection
- [x] Document how to update UDP packets easily from JSON schema
- [x] Browse, play, and delete past recordings from the UI.
- [ ] Improve responsive behavior and gamepad navigation after target-device validation.
- [x] Add backend service installation/startup for Steam Gaming Mode deployment.
- [ ] Add end-to-end transport tests with a simulated WebSocket client and UDP receiver.
