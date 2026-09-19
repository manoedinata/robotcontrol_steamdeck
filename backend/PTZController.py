"""Hikvision ISAPI PTZ control over HTTP.

Translates rotation requests (left/right/up/down) and zoom requests
(zoom-in/zoom-out) into continuous PTZData XML PUT requests, and focus
requests (focus-near/focus-far) into FocusData PUT requests, mirroring the
proven flow from camera_anyar/stream_camera.py. The infrared illuminator is
the odd one out: it is a latched aux control, switched on and off by name
rather than driven while a button is held.
"""

from __future__ import annotations

import asyncio
import logging
import urllib.error
import urllib.request
from typing import Any

import utils

LOGGER = logging.getLogger(__name__)

# Camera keeps moving until a zero-speed PTZData arrives, so the loop re-sends
# the active command (or stop) on this interval as a deadman heartbeat.
PTZ_SEND_HZ = 5.0
REQUEST_TIMEOUT_S = 5.0

MOVE_DIRECTIONS = ("left", "right", "up", "down")
ZOOM_DIRECTIONS = ("zoom-in", "zoom-out")
FOCUS_DIRECTIONS = ("focus-near", "focus-far")

# Fixed camera login for this private deployment; PTZ credentials are not part
# of the settings the UI sends.
PTZ_USERNAME = "admin"
PTZ_PASSWORD = "a1234567"

# Aux control the infrared illuminator answers on. Hikvision numbers aux
# outputs per channel; 1 is the IR light on the deployed camera.
AUX_LIGHT_ID = 1

# Pan and tilt are driven at a multiple of this, chosen by the operator on the
# Home slider: one step is slow enough to frame a subject, and the top step is
# as fast as ISAPI goes, since pan/tilt speed is capped at 100.
PTZ_SPEED = 15
PTZ_SPEED_MULTIPLIER_MAX = 6

ZOOM_SPEED = 60
FOCUS_SPEED = 50

PTZ_STOP_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<PTZData xmlns="http://www.isapi.org/ver20/XMLSchema">'
    "<pan>0</pan><tilt>0</tilt><zoom>0</zoom>"
    "</PTZData>"
)

FOCUS_STOP_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<FocusData xmlns="http://www.isapi.org/ver20/XMLSchema">'
    "<focus>0</focus>"
    "</FocusData>"
)


def aux_light_xml(on: bool) -> str:
    """Build the PTZAux XML that latches the infrared illuminator on or off.

    Unlike PTZData and FocusData this carries no namespace: the camera rejects
    the aux body when one is declared.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<PTZAux>"
        f"<id>{AUX_LIGHT_ID}</id>"
        "<type>LIGHT</type>"
        f"<status>{'on' if on else 'off'}</status>"
        "</PTZAux>"
    )


def direction_to_ptz_data(direction: str, speed: int = PTZ_SPEED) -> str:
    """Build the continuous-move PTZData XML for a cardinal direction."""
    speed = max(1, min(100, int(speed)))
    pan = tilt = 0
    if direction == "up":
        tilt = speed
    elif direction == "down":
        tilt = -speed
    elif direction == "left":
        pan = -speed
    elif direction == "right":
        pan = speed
    else:
        raise ValueError(f"Unknown PTZ direction: {direction}")

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<PTZData xmlns="http://www.isapi.org/ver20/XMLSchema">'
        f"<pan>{pan}</pan>"
        f"<tilt>{tilt}</tilt>"
        f"<zoom>0</zoom>"
        "</PTZData>"
    )


def zoom_to_ptz_data(zoom: str, speed: int = ZOOM_SPEED) -> str:
    """Build the continuous-move PTZData XML for a zoom request."""
    speed = max(1, min(100, int(speed)))
    if zoom == "zoom-in":
        zoom_speed = speed
    elif zoom == "zoom-out":
        zoom_speed = -speed
    else:
        raise ValueError(f"Unknown PTZ zoom request: {zoom}")

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<PTZData xmlns="http://www.isapi.org/ver20/XMLSchema">'
        "<pan>0</pan>"
        "<tilt>0</tilt>"
        f"<zoom>{zoom_speed}</zoom>"
        "</PTZData>"
    )


def focus_to_focus_data_xml(focus: str, speed: int = FOCUS_SPEED) -> str:
    """Build the continuous-move FocusData XML for a focus request.

    Hikvision cameras drive focus through the FocusData endpoint rather than
    the focus field of PTZData, so focus requests get their own XML builder.
    """
    speed = max(1, min(100, int(speed)))
    if focus == "focus-near":
        focus_speed = -speed
    elif focus == "focus-far":
        focus_speed = speed
    else:
        raise ValueError(f"Unknown PTZ focus request: {focus}")

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<FocusData xmlns="http://www.isapi.org/ver20/XMLSchema">'
        f"<focus>{focus_speed}</focus>"
        "</FocusData>"
    )


class PTZController:
    """Sends continuous ISAPI PTZ commands without blocking the event loop.

    A single writer lock serializes camera requests; requests run in a thread
    executor because urllib is synchronous.
    """

    def __init__(
        self,
        ip: str,
        username: str = PTZ_USERNAME,
        password: str = PTZ_PASSWORD,
        channel: int = 1,
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        self.ip = ip
        self.username = username
        self.password = password
        self.channel = channel
        self._url = f"http://{self.ip}/ISAPI/PTZCtrl/channels/{self.channel}/continuous"
        self._focus_url = (
            f"http://{self.ip}/ISAPI/System/Video/inputs/channels/{self.channel}/focus"
        )
        self._aux_url = (
            f"http://{self.ip}/ISAPI/PTZCtrl/channels/{self.channel}"
            f"/auxcontrols/{AUX_LIGHT_ID}"
        )
        self._digest_opener = self._build_opener(digest=True)
        self._basic_opener = self._build_opener(digest=False)
        self._writer_lock = asyncio.Lock()

    def _build_opener(self, digest: bool) -> urllib.request.OpenerDirector:
        mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        # Register credentials at the camera root so both the PTZ and focus
        # endpoints authenticate; urllib only matches sub-URIs of the stored one.
        mgr.add_password(None, f"http://{self.ip}/", self.username, self.password)
        if digest:
            return urllib.request.build_opener(
                urllib.request.HTTPDigestAuthHandler(mgr)
            )
        return urllib.request.build_opener(urllib.request.HTTPBasicAuthHandler(mgr))

    async def _put(self, url: str, xml_body: str) -> None:
        req = urllib.request.Request(
            url,
            data=xml_body.encode("utf-8"),
            method="PUT",
            headers={"Content-Type": "application/xml", "Accept": "*/*"},
        )

        def _open(opener: urllib.request.OpenerDirector) -> None:
            with opener.open(req, timeout=REQUEST_TIMEOUT_S) as resp:
                resp.read()

        last_error: Exception | None = None
        # Digest first, basic fallback on 401, matching stream_camera.py.
        for opener in (self._digest_opener, self._basic_opener):
            try:
                # Detached rather than run_in_executor(None, ...): a camera
                # that has gone away holds a request for REQUEST_TIMEOUT_S,
                # and the default executor is joined when the loop closes, so
                # that wait would be added to every shutdown.
                await utils.run_detached(_open, opener, thread_name="ptz-put")
                return
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code != 401:
                    raise
            except urllib.error.URLError as e:
                last_error = e
                raise
        if last_error is not None:
            raise last_error

    async def move(self, direction: str, multiplier: int = 1) -> None:
        """Start (or keep) rotating in a cardinal direction.

        `multiplier` is the operator's speed step, not a speed: the camera is
        told PTZ_SPEED times that.
        """
        xml = direction_to_ptz_data(direction, PTZ_SPEED * multiplier)
        async with self._writer_lock:
            await self._put(self._url, xml)

    async def zoom(self, zoom: str) -> None:
        """Start (or keep) zooming in or out."""
        xml = zoom_to_ptz_data(zoom)
        async with self._writer_lock:
            await self._put(self._url, xml)

    async def focus(self, focus: str) -> None:
        """Start (or keep) focusing near or far over FocusData."""
        xml = focus_to_focus_data_xml(focus)
        async with self._writer_lock:
            await self._put(self._focus_url, xml)

    async def set_light(self, on: bool) -> None:
        """Switch the infrared illuminator on or off.

        There is no stop to pair with this: the camera holds the state until
        it is told otherwise, so callers send one request per change.
        """
        xml = aux_light_xml(on)
        async with self._writer_lock:
            await self._put(self._aux_url, xml)

    async def stop(self) -> None:
        """Send the zero-speed PTZData that halts continuous motion."""
        async with self._writer_lock:
            await self._put(self._url, PTZ_STOP_XML)

    async def stop_focus(self) -> None:
        """Send the zero-speed FocusData that halts focus motion."""
        async with self._writer_lock:
            await self._put(self._focus_url, FOCUS_STOP_XML)


def normalize_speed_multiplier(value: Any) -> int:
    """Validate the pan/tilt speed step coming from the UI."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("ptz speed multiplier must be an integer")
    if not 1 <= value <= PTZ_SPEED_MULTIPLIER_MAX:
        raise ValueError(
            "ptz speed multiplier must be between 1 and "
            f"{PTZ_SPEED_MULTIPLIER_MAX}, got {value}"
        )
    return value


def normalize_light(value: Any) -> bool:
    """Validate an infrared-light request coming from the UI."""
    if not isinstance(value, bool):
        raise ValueError("ptz light must be true or false")
    return value


def normalize_direction(value: Any) -> str:
    """Validate a rotation request coming from the UI."""
    if not isinstance(value, str):
        raise ValueError("ptz direction must be a string")
    direction = value.strip().lower()
    if direction not in MOVE_DIRECTIONS:
        raise ValueError(
            f"ptz direction must be one of {MOVE_DIRECTIONS}, got {direction!r}"
        )
    return direction


def normalize_zoom(value: Any) -> str:
    """Validate a zoom request coming from the UI."""
    if not isinstance(value, str):
        raise ValueError("ptz zoom must be a string")
    zoom = value.strip().lower()
    if zoom not in ZOOM_DIRECTIONS:
        raise ValueError(f"ptz zoom must be one of {ZOOM_DIRECTIONS}, got {zoom!r}")
    return zoom


def normalize_focus(value: Any) -> str:
    """Validate a focus request coming from the UI."""
    if not isinstance(value, str):
        raise ValueError("ptz focus must be a string")
    focus = value.strip().lower()
    if focus not in FOCUS_DIRECTIONS:
        raise ValueError(f"ptz focus must be one of {FOCUS_DIRECTIONS}, got {focus!r}")
    return focus
