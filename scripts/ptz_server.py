#!/usr/bin/env python3
"""Simulated Hikvision ISAPI PTZ server for testing camera rotation and zoom.

Accepts PUT requests to /ISAPI/PTZCtrl/channels/<channel>/continuous with
PTZData XML bodies and logs each move/zoom/stop request. Intended to run
locally while developing the Steam Deck robot monitor's PTZ integration.

Usage:
    python scripts/ptz_server.py --ip 127.0.0.1 --port 8080
"""

from __future__ import annotations

import argparse
import logging
import xml.etree.ElementTree as ET
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
LOGGER = logging.getLogger(__name__)


class PTZHandler(SimpleHTTPRequestHandler):
    """Handles PTZ control endpoints."""

    def do_PUT(self) -> None:
        if self._is_focus_endpoint():
            self._handle_focus()
            return
        if not self._is_ptz_endpoint():
            self.send_error(404, "Not found")
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
        except Exception as error:
            LOGGER.warning("Failed to read PTZ body: %s", error)
            self.send_error(400, "Invalid body")
            return

        direction = self._parse_direction(body)
        if direction is None:
            LOGGER.info("PTZ stop received (pan=tilt=zoom=0)")
            self._respond_stop()
            return

        action = "ZOOM" if direction.startswith("zoom") else "MOVE"
        LOGGER.info("PTZ %s received: %s", action, direction.upper())
        self._respond_ok()

    def _is_ptz_endpoint(self) -> bool:
        parsed = urlparse(self.path)
        path = parsed.path.lower()
        return "/isapi/ptzctrl/" in path and "/continuous" in path

    def _is_focus_endpoint(self) -> bool:
        parsed = urlparse(self.path)
        path = parsed.path.lower()
        # Matches the backend's focus URL:
        # /ISAPI/System/Video/inputs/channels/<n>/focus
        return "/isapi/system/video/inputs/channels/" in path and path.endswith(
            "/focus"
        )

    def _handle_focus(self) -> None:
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
        except Exception as error:
            LOGGER.warning("Failed to read focus body: %s", error)
            self.send_error(400, "Invalid body")
            return

        speed = self._parse_focus_speed(body)
        if speed is None:
            LOGGER.info("Focus stop received (focus=0)")
            self._respond_ok()
            return
        LOGGER.info("Focus %s received: %s", "NEAR" if speed < 0 else "FAR", speed)
        self._respond_ok()

    def _parse_focus_speed(self, xml_body: str) -> int | None:
        try:
            root = ET.fromstring(xml_body)
            ns = {"ptz": "http://www.isapi.org/ver20/XMLSchema"}
            focus_el = self._find_first(root, ns, "focus")
            speed = int(focus_el.text) if focus_el is not None and focus_el.text else 0
            return speed or None
        except Exception as error:
            LOGGER.warning("Failed to parse focus XML: %s", error)
            return None

    def _parse_direction(self, xml_body: str) -> str | None:
        try:
            root = ET.fromstring(xml_body)
            ns = {"ptz": "http://www.isapi.org/ver20/XMLSchema"}
            # The app sends pan/tilt/zoom inside the ISAPI namespace; fall back
            # to unnamespaced lookups so hand-crafted test bodies also work.
            pan_el = self._find_first(root, ns, "pan")
            tilt_el = self._find_first(root, ns, "tilt")
            zoom_el = self._find_first(root, ns, "zoom")

            pan = int(pan_el.text) if pan_el is not None and pan_el.text else 0
            tilt = int(tilt_el.text) if tilt_el is not None and tilt_el.text else 0
            zoom = int(zoom_el.text) if zoom_el is not None and zoom_el.text else 0

            if pan == 0 and tilt == 0 and zoom == 0:
                return None

            if pan != 0:
                return "left" if pan < 0 else "right"
            if tilt != 0:
                return "up" if tilt > 0 else "down"
            return "zoom-in" if zoom > 0 else "zoom-out"
        except Exception as error:
            LOGGER.warning("Failed to parse PTZ XML: %s", error)
            return None

    @staticmethod
    def _find_first(root: ET.Element, ns: dict, tag: str) -> ET.Element | None:
        # Element.__bool__ is False for childless elements, so an `or` chain
        # would discard a valid match; check for None explicitly instead.
        element = root.find(f".//ptz:{tag}", ns)
        if element is None:
            element = root.find(f".//{tag}")
        return element

    def _respond_ok(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b'<Response xmlns="http://www.isapi.org/ver20/XMLSchema"></Response>'
        )

    def _respond_stop(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b'<Response xmlns="http://www.isapi.org/ver20/XMLSchema"></Response>'
        )

    def log_message(self, fmt: str, *args: object) -> None:
        # Suppress default HTTP access logs from SimpleHTTPServer
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulated Hikvision ISAPI PTZ server")
    parser.add_argument("--ip", default="127.0.0.1", help="Listen address")
    parser.add_argument("--port", type=int, default=8080, help="Listen port")
    args = parser.parse_args()

    server_address = (args.ip, args.port)
    httpd = HTTPServer(server_address, PTZHandler)
    LOGGER.info("PTZ simulator listening on http://%s:%s", args.ip, args.port)
    LOGGER.info("Send PUT requests to /ISAPI/PTZCtrl/channels/1/continuous")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Shutting down PTZ simulator")
        httpd.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
