# Steam Deck Robot Monitor Backend

FastAPI backend that receives robot controls from a UI, sends the latest control packet over UDP at 50 Hz, and proxies an RTSP camera as an MJPEG HTTP stream.

## Architecture

```text
server.py                         FastAPI, WebSocket, UDP, and video streaming
settings.py                       Runtime network settings
utils.py                          Timing and default-packet helpers
../packets-schema.json            Control packet JSON Schema
../scripts/udp_server_simulation.py UDP receiver for local testing
```

The application exposes:

- `WS /ws/controls`: accepts `vy` and `vtheta` control packets plus optional `ip`, `port`, and `rtsp_url` configuration fields.
- `GET /stream`: returns `multipart/x-mixed-replace` MJPEG suitable for an HTML `<img>` element.

UDP transmission starts while at least one controls WebSocket is connected. The sender runs as an asyncio task, caches serialized packet bytes, and avoids CPU-intensive busy waiting. Control state resets after the last UI disconnects.

RTSP capture starts on demand when the first stream viewer connects. One background capture performs JPEG encoding for all viewers, reconnects after failures, and stops after the last viewer disconnects.

## Requirements

- Python 3.10 or newer
- FastAPI
- Uvicorn
- jsonschema
- OpenCV Python

Run Uvicorn with `backend` as the working directory because the backend modules use local imports. The packet schema itself is resolved relative to `server.py`, so it does not depend on the process working directory.

## Control Packet

The packet schema requires both numeric fields:

```json
{"vy": 0.0, "vtheta": 0.0}
```

Configuration and controls may be sent together. Configuration keys are removed before schema validation and are not included in UDP payloads.

## Limitations

- Runtime settings and control state are process-local; use one Uvicorn worker.
- All connected control clients share one packet and destination configuration.
- MJPEG re-encodes RTSP frames and uses more bandwidth than forwarding a compressed H.264/H.265 stream.
- OpenCV backend support for `CAP_PROP_BUFFERSIZE` varies by platform.