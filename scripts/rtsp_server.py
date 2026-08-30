# !/usr/bin/env python3
# RTSP server to stream webcam video to MediaMTX using FFmpeg.
#
# To run, you need FFmpeg and MediaMTX.
# $ apt install ffmpeg
# $ docker run --rm -it --network=host bluenviron/mediamtx:1

import argparse
import cv2
import subprocess
import sys
import time

# --- Configuration ---
# 0 is usually your default laptop webcam. Change to 1 or 2 for external USB cameras.
CAMERA_INDEX = 0
RTSP_BASE_URL = "rtsp://127.0.0.1:8554"
# First path keeps the original name so existing settings.json files still work.
STREAM_PATHS = ["stream", "stream2"]


def main():
    parser = argparse.ArgumentParser(
        description="Publish the webcam to MediaMTX as one or two RTSP streams."
    )
    parser.add_argument(
        "-n",
        "--streams",
        type=int,
        choices=(1, 2),
        default=1,
        help="publish 1 stream, or 2 so the app can switch camera sources",
    )
    args = parser.parse_args()
    urls = [f"{RTSP_BASE_URL}/{path}" for path in STREAM_PATHS[: args.streams]]

    # 1. Open the webcam
    cap = cv2.VideoCapture(CAMERA_INDEX)

    if not cap.isOpened():
        print(f"Error: Could not open webcam at index {CAMERA_INDEX}.")
        sys.exit(1)

    # 2. Get the actual camera resolution and FPS (crucial for FFmpeg formatting)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = int(cap.get(cv2.CAP_PROP_FPS))

    # Fallback to 30 FPS if the camera doesn't report it
    if fps <= 0:
        fps = 30

    print(f"Webcam opened: {width}x{height} @ {fps} FPS")
    print(f"Serving RTSP on: {', '.join(urls)}")
    print("Waiting for a client to connect...")

    def start_ffmpeg(url, mirror):
        """Starts the FFmpeg subprocess to push the webcam to MediaMTX."""
        command = [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{width}x{height}",
            "-r",
            str(fps),
            "-i",
            "-",
            # Mirror the extra stream so switching sources is visibly obvious.
            *(("-vf", "hflip") if mirror else ()),
            # Encoding settings (optimized for low latency)
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            # RTSP Server settings - WE REMOVED THE LISTEN FLAG
            "-f",
            "rtsp",
            url,
        ]

        # Hide FFmpeg's verbose output from the terminal.
        # Change stderr to None if you want to see FFmpeg logs for debugging.
        return subprocess.Popen(command, stdin=subprocess.PIPE, stderr=None)

    pipes = [start_ffmpeg(url, index > 0) for index, url in enumerate(urls)]

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Failed to grab frame from camera.")
                break

            payload = frame.tobytes()
            for index, url in enumerate(urls):
                try:
                    # Write the raw frame bytes to FFmpeg
                    pipes[index].stdin.write(payload)

                except BrokenPipeError:
                    # FFmpeg's 'listen' flag only supports one client session.
                    # When your FastAPI backend disconnects, FFmpeg automatically exits.
                    # We catch the broken pipe and restart FFmpeg to wait for the next connection.
                    print(
                        f"Client disconnected from {url}. Restarting to wait for new connection..."
                    )
                    time.sleep(2)
                    pipes[index] = start_ffmpeg(url, index > 0)

    except KeyboardInterrupt:
        print("\nStopping RTSP server...")

    finally:
        # Clean up resources on exit
        cap.release()
        for pipe in pipes:
            pipe.stdin.close()
            pipe.terminate()
            pipe.wait()


if __name__ == "__main__":
    main()
