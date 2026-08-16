import socket
import time
import threading
import json

from jsonschema import validate, ValidationError
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

import utils
import settings

app = FastAPI()

# Packets
with open("packets-schema.json", "r") as f:
    PACKET_SCHEMA = json.load(f)

current_packet = utils.generate_default_state(PACKET_SCHEMA)

# Settings instance to hold the UDP configuration
settings = settings.Settings("127.0.0.1", 8888)

is_ui_connected = False
state_lock = threading.Lock()


def udp_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = utils.hz_to_ms(50)

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

        # Precision 20ms sleep/spin
        next_time += interval
        sleep_time = next_time - time.perf_counter()
        if sleep_time > 0.002:
            time.sleep(sleep_time - 0.002)
        while time.perf_counter() < next_time:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=udp_loop, daemon=True).start()
    yield


app.router.lifespan_context = lifespan


@app.websocket("/ws/controls")
async def websocket_endpoint(websocket: WebSocket):
    global is_ui_connected

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
                # 1. Pop out network config so it doesn't trigger schema validation errors
                if "ip" in incoming_data:
                    settings.udp_ip = incoming_data.pop("ip")
                if "port" in incoming_data:
                    settings.udp_port = int(incoming_data.pop("port"))

                # 2. Validate the remaining velocity payload
                if incoming_data:
                    try:
                        # Throws a ValidationError if the data doesn't match the schema
                        validate(instance=incoming_data, schema=PACKET_SCHEMA)

                        # Only update if validation passes
                        current_packet.update(incoming_data)

                    except ValidationError as e:
                        # Log the error but keep the connection alive
                        print(f"Ignored invalid velocity packet: {e.message}")

    except WebSocketDisconnect:
        print("Vue UI Disconnected!")

        with state_lock:
            is_ui_connected = False

            # Reset the current packet to default values when the UI disconnects
            current_packet = utils.generate_default_state(PACKET_SCHEMA)
