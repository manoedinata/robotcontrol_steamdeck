import threading
from typing import AsyncIterator
import cv2
import asyncio
import logging

RTSP_RECONNECT_DELAY_S = 1.0
JPEG_QUALITY = 100
MJPEG_BOUNDARY = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"


class MjpegStream:
    """Capture and encode one camera source for all connected HTTP clients."""

    def __init__(self, camera_url: str, logger: logging.Logger | None = None) -> None:
        self._camera_url = camera_url
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame: bytes | None = None
        self._sequence = 0
        self._subscribers = 0

        if not logger:
            logger = logging.getLogger(__name__)
        self._logger = logger

    def update_url(self, camera_url: str) -> None:
        with self._condition:
            self._camera_url = camera_url

    async def frames(self) -> AsyncIterator[bytes]:
        with self._condition:
            self._subscribers += 1
            self._stop_event.clear()
            self._start_worker_locked()
            last_sequence = self._sequence

        try:
            while not self._stop_event.is_set():
                # Lock the condition to safely read the current frame and sequence number
                with self._condition:
                    current_sequence = self._sequence
                    frame = self._frame

                if current_sequence != last_sequence:
                    last_sequence = current_sequence
                    if frame is not None:
                        yield MJPEG_BOUNDARY + frame + b"\r\n"
                else:
                    # Bring back control to the Uvicorn Event Loop.
                    # This allows Uvicorn to cancel this task instantly on Ctrl-C.
                    await asyncio.sleep(0.02)  # ~50 FPS polling rate
        finally:
            with self._condition:
                self._subscribers -= 1
                if self._subscribers == 0:
                    self._stop_event.set()
                    self._condition.notify_all()

    def close(self) -> None:
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()

        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)

    def _start_worker_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._thread = threading.Thread(
            target=self._capture_loop,
            name="camera-capture",
            daemon=True,
        )
        self._thread.start()

    def _capture_loop(self) -> None:
        capture: cv2.VideoCapture | None = None
        capture_url = ""

        try:
            while not self._stop_event.is_set():
                with self._condition:
                    configured_url = self._camera_url

                if not configured_url:
                    if capture is not None:
                        capture.release()
                        capture = None
                    capture_url = ""
                    self._stop_event.wait(RTSP_RECONNECT_DELAY_S)
                    continue

                if capture is None or configured_url != capture_url:
                    if capture is not None:
                        capture.release()
                    capture_url = configured_url

                    capture = cv2.VideoCapture(
                        capture_url,
                        cv2.CAP_FFMPEG,
                        # params=[
                        #     cv2.CAP_PROP_HW_ACCELERATION,
                        #     cv2.VIDEO_ACCELERATION_ANY,
                        # ],
                    )

                    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                    if not capture.isOpened():
                        self._logger.warning("Unable to open camera stream; retrying")
                        capture.release()
                        capture = None
                        self._stop_event.wait(RTSP_RECONNECT_DELAY_S)
                        continue

                # Rapidly drain the buffer to ensure we only decode the absolute latest frame
                success = capture.grab()
                if not success:
                    self._logger.warning("Camera frame grab failed; reconnecting")
                    capture.release()
                    capture = None
                    self._stop_event.wait(RTSP_RECONNECT_DELAY_S)
                    continue

                # Retrieve the actual image data from the last grab
                _, frame = capture.retrieve()

                # Reduce resolution
                # frame = cv2.resize(frame, (640, 480))

                encoded, buffer = cv2.imencode(
                    ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
                )
                if not encoded:
                    self._logger.warning("JPEG frame encoding failed")
                    continue

                with self._condition:
                    self._frame = buffer.tobytes()
                    self._sequence += 1
                    self._condition.notify_all()
        finally:
            if capture is not None:
                capture.release()

            with self._condition:
                self._thread = None
                if self._subscribers > 0 and not self._stop_event.is_set():
                    self._start_worker_locked()
                self._condition.notify_all()
