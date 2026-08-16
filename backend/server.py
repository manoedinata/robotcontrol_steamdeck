import socket
import time
import threading
import json
import cv2  # Added for RTSP streaming

from jsonschema import validate, ValidationError
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse  # Added for the video feed

import utils
import settings

app = FastAPI()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the UDP loop in the background
    threading.Thread(target=udp_loop, daemon=True).start()
    yield


app.router.lifespan_context = lifespan

# Packets
with open("../packets-schema.json", "r") as f:
    PACKET_SCHEMA = json.load(f)

current_packet = utils.generate_default_state(PACKET_SCHEMA)
settings = settings.Settings(
    udp_ip="127.0.0.1",
    udp_port=8888,
    rtsp_url="rtsp://admin:password@127.0.0.1:554/stream",
)

is_ui_connected = False
state_lock = threading.Lock()


# --- Video Streaming Logic ---
def generate_frames():
    """Reads frames from the RTSP stream and encodes them as MJPEG."""
    cap = cv2.VideoCapture(settings.rtsp_url)

    # Optional: reduce buffer size to lower latency
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    try:
        while True:
            success, frame = cap.read()
            if not success:
                # If stream drops, you could add a small sleep and try to reconnect
                print(
                    "Failed to read frame from RTSP stream. Attempting to reconnect..."
                )
                time.sleep(1)  # Wait a second before trying to reconnect
                cap.release()  # Release the current capture
                cap = cv2.VideoCapture(settings.rtsp_url)  # Attempt to reconnect
                break

            # Compress to JPEG (Adjust quality 0-100 as needed)
            ret, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            frame_bytes = buffer.tobytes()

            # Yield the multipart boundary and image data
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
            )
    finally:
        # Ensure the camera is released when the client disconnects
        cap.release()


@app.get("/stream")
def video_feed():
    """Endpoint for the Vue UI to bind to the <img src="..."> tag."""
    return StreamingResponse(
        generate_frames(), media_type="multipart/x-mixed-replace; boundary=frame"
    )


def udp_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = utils.hz_to_s(50)

    next_time = time.perf_counter() + interval

    while True:
        send_data = False
        payload = b""
        target_ip = ""
        target_port = 0

        # Safely grab the latest velocity AND destination config
        with state_lock:
            if is_ui_connected:
                payload = json.dumps(current_packet).encode("utf-8")
                target_ip = settings.udp_ip
                target_port = settings.udp_port
                send_data = True

        if send_data:
            try:
                sock.sendto(payload, (target_ip, target_port))
            except Exception as e:
                print(f"UDP Send Error to {target_ip}:{target_port} - {e}")

        # Precision sleep/spin
        next_time += interval
        sleep_time = next_time - time.perf_counter()
        if sleep_time > (interval / 10):
            time.sleep(sleep_time - (interval / 10))
        while time.perf_counter() < next_time:
            pass


@app.websocket("/ws/controls")
async def websocket_endpoint(websocket: WebSocket):
    global is_ui_connected
    global current_packet  # Ensure we modify the global packet, not a local copy!

    await websocket.accept()
    print("Vue UI Connected!")

    with state_lock:
        print(
            f"Sending packet with {utils.hz_to_ms(50):.2f} ms interval to {settings.udp_ip}:{settings.udp_port}"
        )
        is_ui_connected = True

    try:
        while True:
            data = await websocket.receive_text()
            incoming_data = json.loads(data)

            with state_lock:
                # Pop out network config so it doesn't trigger schema validation errors
                if "ip" in incoming_data:
                    settings.udp_ip = incoming_data.pop("ip")
                if "port" in incoming_data:
                    settings.udp_port = int(incoming_data.pop("port"))
                if "rtsp_url" in incoming_data:
                    settings.rtsp_url = incoming_data.pop("rtsp_url")

                # Validate the remaining velocity payload
                if incoming_data:
                    try:
                        # Throws a ValidationError if the data doesn't match the schema
                        validate(instance=incoming_data, schema=PACKET_SCHEMA)
                        # Only update if validation passes
                        current_packet.update(incoming_data)
                    except ValidationError as e:
                        print(f"Ignored invalid velocity packet: {e.message}")

    except WebSocketDisconnect:
        print("Vue UI Disconnected!")

        with state_lock:
            is_ui_connected = False
            # Reset the current packet to default values when the UI disconnects
            current_packet = utils.generate_default_state(PACKET_SCHEMA)
