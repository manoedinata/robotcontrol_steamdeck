# TODO

## Transport

- [x] Move UDP sending and camera capture from Electron to FastAPI.
- [x] Connect Vue to FastAPI through typed WebSocket messages.
- [x] Encode UDP commands from the shared ordered binary schema.
- [ ] Add a separate backend UDP receive path and typed robot telemetry messages when the telemetry contract is defined. Receiving data uses a specific listening port.
- [ ] Add sequence IDs if command acknowledgement, exact RTT, or loss measurements are required.

## Product

- [x] Username & password support for RTSP connection
- [ ] Document how to update UDP packets easily from JSON schema
- [ ] Improve responsive behavior and gamepad navigation after target-device validation.
- [ ] Add backend service installation/startup for Steam Gaming Mode deployment.
- [ ] Add end-to-end transport tests with a simulated WebSocket client and UDP receiver.
