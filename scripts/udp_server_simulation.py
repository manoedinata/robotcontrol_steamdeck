import socket
import time
import struct

UDP_IP = "127.0.0.1"  # Change to "0.0.0.0" if receiving from another computer
UDP_PORT = 8888

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

print(f"Listening for UDP telemetry on {UDP_IP}:{UDP_PORT}...")

# Tracking variables
packet_count = 0
window_start = time.perf_counter()
last_arrival = time.perf_counter()

# Lists to hold data for the 1-second rolling average
latencies = []
intervals = []

while True:
    data, addr = sock.recvfrom(1024)
    now_perf = time.perf_counter()  # For highly accurate interval math
    now_sys = time.time()  # For latency comparison against client timestamp

    # 1. Calculate Inter-packet Interval (Spacing)
    interval_ms = (now_perf - last_arrival) * 1000.0
    intervals.append(interval_ms)
    last_arrival = now_perf

    # 2. Calculate Latency
    # We expect the first 8 bytes of the packet to be a 'double' (the timestamp)
    if len(data) >= 8:
        try:
            sent_time = struct.unpack("d", data[:8])[0]
            latency_ms = (now_sys - sent_time) * 1000.0
            latencies.append(latency_ms)
        except struct.error:
            pass  # Ignore malformed packets

    packet_count += 1

    # # 3. Print stats every 1 second
    # if now_perf - window_start >= 1.0:
    hz = packet_count / (now_perf - window_start)

    avg_interval = sum(intervals) / len(intervals) if intervals else 0
    max_interval = max(intervals) if intervals else 0
    avg_latency = sum(latencies) / len(latencies) if latencies else 0

    print(f"--- Stats for last second ---")
    print(f"Rate:        {hz:.2f} Hz")
    print(f"Avg Spacing: {avg_interval:.2f} ms (Target: 20.00 ms)")
    print(f"Max Spacing: {max_interval:.2f} ms (Jitter spike)")
    if latencies:
        print(f"Avg Latency: {avg_latency:.2f} ms (One-way)")
    print("-" * 29)

    # Reset window
    packet_count = 0
    window_start = now_perf
    latencies.clear()
    intervals.clear()
