import asyncio
import logging
import time
import unittest
from unittest import mock

from aiortc import MediaStreamTrack
from aiortc.mediastreams import MediaStreamError

from PTZController import (
    AUX_LIGHT_ID,
    PTZ_SPEED,
    PTZ_SPEED_MULTIPLIER_MAX,
    PTZController,
    aux_light_xml,
    direction_to_ptz_data,
    focus_to_focus_data_xml,
    normalize_direction,
    normalize_focus,
    normalize_light,
    normalize_speed_multiplier,
    normalize_zoom,
    zoom_to_ptz_data,
)
from server import (
    SCHEMA_SLEW_RATES,
    advance_packet,
    encode_current_packet,
    ptz_loop,
    restarted_camera_streams,
    udp_loop,
    runtime,
    send_field_limits,
    validate_config,
    validate_recordings_dir,
)
from CameraWebSocketSource import (
    CameraWebSocketHub,
    PREROLL_MAX_BYTES,
    PREROLL_MAX_PAYLOADS,
    PrerollBuffer,
    START_CODE,
    SUBSCRIBER_QUEUE_SIZE,
    annexb_nal_types,
    carries_picture,
    detect_payload_format,
    is_websocket_url,
    reconnect_delay,
    relay_url,
    should_forward,
)
import WebRTCStream as camera_backend
from WebRTCStream import (
    RTSP_TRANSPORTS,
    STALL_CHECK_INTERVAL_S,
    STALL_TIMEOUT_S,
    _AiortcStream,
    _cancel_task,
    _resolve_stream_id,
    _SharedPlayer,
    WebRTCStream,
    go2rtc_source,
    ingest_url,
    missing_parameter_sets,
    rtsp_open_options,
)


def _nal(nal_type: int, body: bytes = b"\x0a" * 8) -> bytes:
    """One Annex-B NAL unit of the given type."""
    return START_CODE + bytes([nal_type]) + body


class CameraBackendConfigTests(unittest.TestCase):
    def test_go2rtc_is_the_default_backend(self) -> None:
        config = validate_config({})
        self.assertEqual(config[4], "go2rtc")

    def test_supported_backend_names_are_accepted(self) -> None:
        for backend in ("go2rtc", "aiortc"):
            with self.subTest(backend=backend):
                self.assertEqual(
                    validate_config({"camera_backend": backend})[4], backend
                )

    def test_unknown_backend_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_config({"camera_backend": "ffmpeg"})


class RtspTransportConfigTests(unittest.TestCase):
    """How the live path is told to carry RTP."""

    def test_defaults_to_tcp(self) -> None:
        # The transport that survives a VPN or a filtered network, where UDP
        # leaves a camera connected and silent.
        self.assertEqual(validate_config({})[9], "tcp")

    def test_known_transports_are_accepted(self) -> None:
        for transport in RTSP_TRANSPORTS:
            with self.subTest(transport=transport):
                self.assertEqual(
                    validate_config({"rtsp_transport": transport})[9], transport
                )

    def test_unknown_transport_is_rejected(self) -> None:
        for bad in ("http", "TCP", "", 4):
            with self.subTest(transport=bad):
                with self.assertRaises(ValueError):
                    validate_config({"rtsp_transport": bad})

    def test_the_transport_reaches_the_dial_options(self) -> None:
        self.assertEqual(
            rtsp_open_options("5000000", "udp")["rtsp_transport"], "udp"
        )


class CameraStreamsConfigTests(unittest.TestCase):
    def test_defaults_to_no_streams(self) -> None:
        self.assertEqual(validate_config({})[3], ())

    def test_valid_streams_are_normalized_to_id_url_pairs(self) -> None:
        streams = validate_config(
            {
                "camera_streams": [
                    {"id": "cam-0", "url": "  rtsp://user:pw@10.0.0.5:554/s  "},
                    {"id": "cam-1", "url": "rtsp://10.0.0.6/live"},
                ]
            }
        )[3]
        self.assertEqual(
            streams,
            (
                ("cam-0", "rtsp://user:pw@10.0.0.5:554/s"),
                ("cam-1", "rtsp://10.0.0.6/live"),
            ),
        )

    def test_empty_list_clears_streams(self) -> None:
        self.assertEqual(validate_config({"camera_streams": []})[3], ())

    def test_accepts_websocket_sources_alongside_rtsp(self) -> None:
        streams = validate_config(
            {
                "camera_streams": [
                    {"id": "cam-0", "url": "rtsp://10.0.0.5/s"},
                    {"id": "cam-1", "url": "ws://192.168.1.123:12351"},
                    {"id": "cam-2", "url": "wss://192.168.1.124:12351"},
                ]
            }
        )[3]
        self.assertEqual(
            streams,
            (
                ("cam-0", "rtsp://10.0.0.5/s"),
                ("cam-1", "ws://192.168.1.123:12351"),
                ("cam-2", "wss://192.168.1.124:12351"),
            ),
        )

    def test_rejects_a_url_that_is_neither_rtsp_nor_websocket(self) -> None:
        for bad_url in ("http://x/y", "https://x/y", "file:///etc/passwd"):
            with self.subTest(bad_url=bad_url), self.assertRaises(ValueError):
                validate_config(
                    {"camera_streams": [{"id": "cam-0", "url": bad_url}]}
                )

    def test_rejects_a_url_without_a_hostname(self) -> None:
        for bad_url in ("rtsp://", "ws://", "ws:///path"):
            with self.subTest(bad_url=bad_url), self.assertRaises(ValueError):
                validate_config(
                    {"camera_streams": [{"id": "cam-0", "url": bad_url}]}
                )

    def test_rejects_bad_stream_id(self) -> None:
        for bad_id in ("cam 0", "cam/0", "", "x" * 65):
            with self.subTest(bad_id=bad_id), self.assertRaises(ValueError):
                validate_config(
                    {"camera_streams": [{"id": bad_id, "url": "rtsp://h/s"}]}
                )

    def test_rejects_duplicate_stream_ids(self) -> None:
        with self.assertRaises(ValueError):
            validate_config(
                {
                    "camera_streams": [
                        {"id": "cam-0", "url": "rtsp://h/a"},
                        {"id": "cam-0", "url": "rtsp://h/b"},
                    ]
                }
            )

    def test_rejects_non_list_and_legacy_camera_url(self) -> None:
        with self.assertRaises(ValueError):
            validate_config({"camera_streams": "rtsp://h/s"})
        with self.assertRaises(ValueError):
            validate_config({"camera_url": "rtsp://h/s"})


class ResolveStreamIdTests(unittest.TestCase):
    def test_named_stream_is_returned_when_it_exists(self) -> None:
        streams = {"cam-0": "rtsp://h/a", "cam-1": "rtsp://h/b"}
        self.assertEqual(_resolve_stream_id(streams, "cam-1"), "cam-1")

    def test_named_stream_that_is_unknown_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _resolve_stream_id({"cam-0": "rtsp://h/a"}, "cam-9")

    def test_missing_id_falls_back_only_with_a_single_stream(self) -> None:
        self.assertEqual(_resolve_stream_id({"cam-0": "rtsp://h/a"}, None), "cam-0")
        with self.assertRaises(ValueError):
            _resolve_stream_id({"a": "rtsp://h/a", "b": "rtsp://h/b"}, None)
        with self.assertRaises(ValueError):
            _resolve_stream_id({}, None)


class SendFieldLimitsTests(unittest.TestCase):
    def test_padding_is_excluded_and_bounds_are_announced(self) -> None:
        fields = send_field_limits()
        names = [field["name"] for field in fields]

        self.assertNotIn("padding", names)
        self.assertEqual(names, ["pwm", "steering"])
        for field in fields:
            with self.subTest(field=field["name"]):
                self.assertEqual(
                    set(field),
                    {"name", "role", "type", "min", "max", "default", "slew_rate"},
                )
                self.assertLess(field["min"], field["max"])
                # The schema gives both command fields a ramp rate.
                self.assertGreater(field["slew_rate"], 0)


class PacketSlewConfigTests(unittest.TestCase):
    def test_schema_rates_apply_when_the_operator_overrides_nothing(self) -> None:
        self.assertEqual(validate_config({})[7], {})
        self.assertEqual(
            {field["name"]: field["slew_rate"] for field in send_field_limits()},
            SCHEMA_SLEW_RATES,
        )

    def test_overrides_are_accepted_and_zero_disables_limiting(self) -> None:
        rates = validate_config({"packet_slew": {"pwm": 40, "steering": 0}})[7]
        self.assertEqual(rates, {"pwm": 40.0, "steering": 0.0})
        for rate in rates.values():
            self.assertIsInstance(rate, float)

    def test_unknown_padding_and_negative_rates_are_rejected(self) -> None:
        for slew in (
            {"nope": 10},
            {"padding": 10},
            {"pwm": -1},
            {"pwm": float("inf")},
            {"pwm": True},
            {"pwm": "fast"},
            [],
        ):
            with self.subTest(slew=slew):
                with self.assertRaises(ValueError):
                    validate_config({"packet_slew": slew})


class RampTests(unittest.TestCase):
    """The send loop's side of the ramp, against the real packet schema."""

    def setUp(self) -> None:
        self.saved = (dict(runtime.target_packet), dict(runtime.current_packet))

    def tearDown(self) -> None:
        target, current = self.saved
        runtime.target_packet = target
        runtime.current_packet = current

    def test_one_tick_moves_a_field_by_at_most_its_rate(self) -> None:
        runtime.current_packet["pwm"] = 0.0
        runtime.target_packet["pwm"] = 100

        self.assertTrue(advance_packet(0.02))
        self.assertAlmostEqual(
            runtime.current_packet["pwm"], SCHEMA_SLEW_RATES["pwm"] * 0.02
        )
        self.assertFalse(advance_packet(0.0))  # no time passed, no movement

    def test_a_mid_ramp_packet_encodes_whatever_the_field_types_are(self) -> None:
        # Ramp state is float even for an integer field, so the encoder has to
        # be handed a rounded value rather than the raw state.
        for field in send_field_limits():
            runtime.current_packet[field["name"]] = 4.7

        self.assertEqual(len(encode_current_packet()), len(runtime.packet_payload))


class PTZConfigTests(unittest.TestCase):
    def test_ptz_defaults_to_disabled(self) -> None:
        # An empty ptz_ip is what disables PTZ; the hardcoded credentials
        # only take effect once an address is configured.
        config = validate_config({})
        self.assertEqual(config[6], "")

    def test_ptz_ip_is_accepted_and_stripped(self) -> None:
        config = validate_config({"ptz_ip": "  192.168.1.64  "})
        self.assertEqual(config[6], "192.168.1.64")

    def test_unknown_ptz_field_is_rejected(self) -> None:
        # Credentials are hardcoded in the backend, so they are no longer
        # accepted on the wire.
        for field in ("ptz_host", "ptz_username", "ptz_password"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_config({field: "192.168.1.64"})

    def test_non_string_ptz_ip_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_config({"ptz_ip": 123})


class PTZDirectionTests(unittest.TestCase):
    def test_cardinal_directions_map_to_pan_tilt(self) -> None:
        # What is under test is which axis moves and which way, not how fast:
        # the speed is the operator's, one step of it by default.
        expected = {
            "left": f"<pan>-{PTZ_SPEED}</pan><tilt>0</tilt>",
            "right": f"<pan>{PTZ_SPEED}</pan><tilt>0</tilt>",
            "up": f"<pan>0</pan><tilt>{PTZ_SPEED}</tilt>",
            "down": f"<pan>0</pan><tilt>-{PTZ_SPEED}</tilt>",
        }
        for direction, fragment in expected.items():
            with self.subTest(direction=direction):
                self.assertIn(fragment, direction_to_ptz_data(direction))

    def test_speed_is_clamped(self) -> None:
        self.assertIn("<pan>100</pan>", direction_to_ptz_data("right", 500))
        self.assertIn("<pan>1</pan>", direction_to_ptz_data("right", -5))

    def test_normalize_direction_rejects_unknown_values(self) -> None:
        for value in ("spin", "", None, 3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_direction(value)

    def test_normalize_direction_accepts_cardinals(self) -> None:
        for direction in ("left", "right", "up", "down"):
            with self.subTest(direction=direction):
                self.assertEqual(normalize_direction(direction), direction)

    def test_normalize_direction_trims_and_lowercases(self) -> None:
        self.assertEqual(normalize_direction("  LEFT "), "left")


class PTZSpeedTests(unittest.TestCase):
    """The operator's speed step, which scales pan and tilt only."""

    def test_one_step_is_the_base_speed(self) -> None:
        self.assertEqual(PTZ_SPEED, 15)
        self.assertIn(
            f"<pan>{PTZ_SPEED}</pan>", direction_to_ptz_data("right", PTZ_SPEED)
        )

    def test_the_top_step_stays_inside_the_isapi_range(self) -> None:
        # The slider stops at the last step that is a whole multiple of the
        # base: one more would be clamped and read as the same speed twice.
        top = PTZ_SPEED * PTZ_SPEED_MULTIPLIER_MAX
        self.assertLessEqual(top, 100)
        self.assertGreater(PTZ_SPEED * (PTZ_SPEED_MULTIPLIER_MAX + 1), 100)
        self.assertIn(f"<tilt>{top}</tilt>", direction_to_ptz_data("up", top))

    def test_normalize_rejects_values_off_the_slider(self) -> None:
        for value in (0, -1, PTZ_SPEED_MULTIPLIER_MAX + 1, 1.5, "2", True, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_speed_multiplier(value)

    def test_normalize_accepts_every_step(self) -> None:
        for value in range(1, PTZ_SPEED_MULTIPLIER_MAX + 1):
            with self.subTest(value=value):
                self.assertEqual(normalize_speed_multiplier(value), value)


class PTZZoomTests(unittest.TestCase):
    def test_zoom_requests_map_to_zoom_axis(self) -> None:
        self.assertIn(
            "<pan>0</pan><tilt>0</tilt><zoom>60</zoom>",
            zoom_to_ptz_data("zoom-in"),
        )
        self.assertIn(
            "<pan>0</pan><tilt>0</tilt><zoom>-60</zoom>",
            zoom_to_ptz_data("zoom-out"),
        )

    def test_zoom_speed_is_clamped(self) -> None:
        self.assertIn("<zoom>100</zoom>", zoom_to_ptz_data("zoom-in", 500))
        self.assertIn("<zoom>-1</zoom>", zoom_to_ptz_data("zoom-out", -5))

    def test_zoom_rejects_unknown_values(self) -> None:
        for value in ("in", "wide", "", None, 3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                zoom_to_ptz_data(value)

    def test_normalize_zoom_rejects_unknown_values(self) -> None:
        for value in ("in", "out", "", None, 3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_zoom(value)

    def test_normalize_zoom_accepts_known_values(self) -> None:
        for zoom in ("zoom-in", "zoom-out"):
            with self.subTest(zoom=zoom):
                self.assertEqual(normalize_zoom(zoom), zoom)

    def test_normalize_zoom_trims_and_lowercases(self) -> None:
        self.assertEqual(normalize_zoom("  ZOOM-IN "), "zoom-in")


class PTZFocusTests(unittest.TestCase):
    def test_focus_requests_map_to_focus_data_xml(self) -> None:
        self.assertIn(
            '<FocusData xmlns="http://www.isapi.org/ver20/XMLSchema">'
            "<focus>-50</focus>",
            focus_to_focus_data_xml("focus-near"),
        )
        self.assertIn(
            '<FocusData xmlns="http://www.isapi.org/ver20/XMLSchema">'
            "<focus>50</focus>",
            focus_to_focus_data_xml("focus-far"),
        )

    def test_focus_speed_is_clamped(self) -> None:
        self.assertIn("<focus>100</focus>", focus_to_focus_data_xml("focus-far", 500))
        self.assertIn("<focus>-1</focus>", focus_to_focus_data_xml("focus-near", -5))

    def test_focus_rejects_unknown_values(self) -> None:
        for value in ("near", "tele", "", None, 3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                focus_to_focus_data_xml(value)

    def test_normalize_focus_rejects_unknown_values(self) -> None:
        for value in ("near", "far", "", None, 3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_focus(value)

    def test_normalize_focus_accepts_known_values(self) -> None:
        for focus in ("focus-near", "focus-far"):
            with self.subTest(focus=focus):
                self.assertEqual(normalize_focus(focus), focus)

    def test_normalize_focus_trims_and_lowercases(self) -> None:
        self.assertEqual(normalize_focus("  FOCUS-NEAR "), "focus-near")


class PTZLightTests(unittest.TestCase):
    """The infrared illuminator: latched aux control, not a held button."""

    def test_light_requests_map_to_aux_xml(self) -> None:
        self.assertIn(
            f"<PTZAux><id>{AUX_LIGHT_ID}</id><type>LIGHT</type><status>on</status>",
            aux_light_xml(True),
        )
        self.assertIn("<status>off</status>", aux_light_xml(False))

    def test_light_xml_declares_no_namespace(self) -> None:
        # The camera rejects the aux body when one is declared, unlike PTZData.
        self.assertNotIn("xmlns", aux_light_xml(True))

    def test_light_uses_the_aux_control_endpoint(self) -> None:
        controller = PTZController(ip="10.0.0.9")
        self.assertEqual(
            controller._aux_url,
            f"http://10.0.0.9/ISAPI/PTZCtrl/channels/1/auxcontrols/{AUX_LIGHT_ID}",
        )

    def test_normalize_light_rejects_non_booleans(self) -> None:
        for value in ("on", "true", 1, 0, None, ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_light(value)

    def test_normalize_light_accepts_booleans(self) -> None:
        self.assertIs(normalize_light(True), True)
        self.assertIs(normalize_light(False), False)


class _FakePTZ:
    """Records what the deadman loop asks of a camera, without a camera."""

    ip = "10.0.0.9"

    def __init__(self, failed_lights: int = 0) -> None:
        self.moves: list[tuple[str, int]] = []
        self.lights: list[bool] = []
        self._failed_lights = failed_lights

    async def move(self, direction: str, multiplier: int = 1) -> None:
        self.moves.append((direction, multiplier))

    async def zoom(self, zoom: str) -> None:
        pass

    async def focus(self, focus: str) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def stop_focus(self) -> None:
        pass

    async def set_light(self, on: bool) -> None:
        self.lights.append(on)
        if self._failed_lights > 0:
            self._failed_lights -= 1
            raise RuntimeError("camera refused the aux control")


class _FakeSocket:
    """Stands in for the bound socket the send and receive loops share."""

    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, payload: bytes, address: tuple[str, int]) -> None:
        self.sent.append((payload, address))


class UDPSendLoopTests(unittest.IsolatedAsyncioTestCase):
    """The 50 Hz sender keeps running, whatever else is added beside it."""

    def setUp(self) -> None:
        self._saved = (
            dict(runtime.clients),
            runtime.udp_enabled,
            runtime.udp_socket,
        )
        runtime.clients = {object(): asyncio.Lock()}
        runtime.udp_enabled = True

    def tearDown(self) -> None:
        clients, enabled, sock = self._saved
        runtime.clients = clients
        runtime.udp_enabled = enabled
        runtime.udp_socket = sock

    async def test_a_connected_ui_keeps_datagrams_going_out(self) -> None:
        # This loop swallows its own exceptions so a bad tick cannot kill the
        # task silently, which also means a mistake in it goes unheard: the UI
        # stays connected, nothing is logged twice, and the robot simply stops
        # being driven. So it is exercised end to end rather than by reading.
        sock = _FakeSocket()
        runtime.udp_socket = sock
        loop_task = asyncio.create_task(udp_loop())
        try:
            await asyncio.sleep(0.1)
        finally:
            await _cancel_task(loop_task)

        self.assertGreater(len(sock.sent), 1)
        payload, address = sock.sent[0]
        self.assertEqual(payload, runtime.packet_payload)
        self.assertEqual(
            address, (runtime.config.udp_ip, runtime.config.udp_port)
        )


class PTZSpeedLoopTests(unittest.IsolatedAsyncioTestCase):
    """The speed step the UI last sent is what the deadman keeps re-sending."""

    def setUp(self) -> None:
        self._saved = (
            runtime.ptz,
            runtime.ptz_request,
            runtime.ptz_speed_multiplier,
            runtime.ptz_light_request,
            runtime.ptz_light_sent,
        )
        runtime.ptz_light_request = False
        runtime.ptz_light_sent = False

    def tearDown(self) -> None:
        (
            runtime.ptz,
            runtime.ptz_request,
            runtime.ptz_speed_multiplier,
            runtime.ptz_light_request,
            runtime.ptz_light_sent,
        ) = self._saved

    async def test_the_held_direction_carries_the_speed_step(self) -> None:
        controller = _FakePTZ()
        runtime.ptz = controller
        runtime.ptz_request = "left"
        runtime.ptz_speed_multiplier = 3
        loop_task = asyncio.create_task(ptz_loop())
        try:
            await asyncio.sleep(0.05)
            # A slider moved mid-hold reaches the camera on the next tick,
            # without waiting for the button to be released and pressed again.
            runtime.ptz_speed_multiplier = 6
            await asyncio.sleep(0.3)
        finally:
            await _cancel_task(loop_task)

        self.assertEqual(controller.moves[0], ("left", 3))
        self.assertEqual(controller.moves[-1], ("left", 6))


class PTZLightLoopTests(unittest.IsolatedAsyncioTestCase):
    """The light is latched, so the loop speaks only when it changes."""

    def setUp(self) -> None:
        self._saved = (
            runtime.ptz,
            runtime.ptz_light_request,
            runtime.ptz_light_sent,
        )
        runtime.ptz_light_request = False
        runtime.ptz_light_sent = None

    def tearDown(self) -> None:
        (
            runtime.ptz,
            runtime.ptz_light_request,
            runtime.ptz_light_sent,
        ) = self._saved

    async def test_the_light_is_sent_on_change_and_not_every_tick(self) -> None:
        controller = _FakePTZ()
        runtime.ptz = controller
        loop_task = asyncio.create_task(ptz_loop())
        try:
            # One command states the light the UI is showing, so a camera that
            # was left lit by a previous session is put back in step.
            await asyncio.sleep(0.05)
            self.assertEqual(controller.lights, [False])

            runtime.ptz_light_request = True
            await asyncio.sleep(0.3)
            self.assertEqual(controller.lights, [False, True])

            # Several ticks pass with nothing new asked for: the aux endpoint
            # hears nothing, unlike the rotation deadman beside it.
            await asyncio.sleep(0.5)
            self.assertEqual(controller.lights, [False, True])
        finally:
            await _cancel_task(loop_task)

    async def test_a_failed_light_command_is_retried(self) -> None:
        # There is no stop behind this one, so a lost command would otherwise
        # leave the camera dark until the operator pressed the button twice.
        controller = _FakePTZ(failed_lights=1)
        runtime.ptz = controller
        runtime.ptz_light_sent = False
        runtime.ptz_light_request = True
        loop_task = asyncio.create_task(ptz_loop())
        try:
            await asyncio.sleep(0.5)
            self.assertEqual(controller.lights, [True, True])
            self.assertIs(runtime.ptz_light_sent, True)
        finally:
            await _cancel_task(loop_task)



class CameraIngestTests(unittest.TestCase):
    """How a configured source is turned into something a backend can open."""

    def test_rtsp_sources_are_dialed_unchanged(self) -> None:
        url = "rtsp://user:pw@10.0.0.5:554/s"
        self.assertFalse(is_websocket_url(url))
        self.assertEqual(ingest_url("cam-0", url), url)

    def test_websocket_sources_are_read_from_the_local_relay(self) -> None:
        for url in ("ws://192.168.1.123:12351", "wss://192.168.1.123:12351"):
            with self.subTest(url=url):
                self.assertTrue(is_websocket_url(url))
                self.assertEqual(
                    ingest_url("cam-1", url),
                    "http://127.0.0.1:8000/camera/cam-1/stream",
                )

    def test_the_relay_url_names_the_stream_it_serves(self) -> None:
        self.assertEqual(
            relay_url("cam-2"), "http://127.0.0.1:8000/camera/cam-2/stream"
        )

    def test_rtsp_is_dialed_over_tcp(self) -> None:
        # A VPN or a filtered network drops RTP over UDP, and the camera then
        # completes the handshake and sends nothing. The recorder has always
        # dialed TCP; the live view must agree, or a recordable source is one
        # the operator cannot watch.
        options = rtsp_open_options("5000000")
        self.assertEqual(options["rtsp_transport"], "tcp")
        self.assertEqual(options["timeout"], "5000000")

    def test_the_live_path_is_never_prerolled(self) -> None:
        # go2rtc opens this url for the live view; replaying a buffered GOP
        # into it would start the view on video that is already seconds old.
        self.assertNotIn("preroll", relay_url("cam-2"))
        self.assertNotIn("preroll", ingest_url("cam-1", "ws://192.168.1.123:12351"))
        self.assertNotIn("preroll", go2rtc_source("cam-1", "ws://h:1/", "h264"))

    def test_a_recording_asks_the_relay_for_the_buffered_keyframe(self) -> None:
        self.assertEqual(
            relay_url("cam-2", preroll=True),
            "http://127.0.0.1:8000/camera/cam-2/stream?preroll=1",
        )

    def test_go2rtc_registers_an_rtsp_source_as_a_plain_url(self) -> None:
        url = "rtsp://10.0.0.5/s"
        self.assertEqual(go2rtc_source("cam-0", url, "h264"), url)

    def test_go2rtc_registers_a_relayed_source_with_the_detected_demuxer(self) -> None:
        source = go2rtc_source("cam-1", "ws://192.168.1.123:12351", "mjpeg")
        self.assertTrue(source.startswith("exec:ffmpeg "))
        # The relay serves a bare bytestream, so the demuxer has to be named
        # and it has to come before the input.
        self.assertIn("-f mjpeg -i http://127.0.0.1:8000/camera/cam-1/stream", source)
        self.assertIn("-c:v copy", source)
        self.assertTrue(source.endswith("-f rtsp {output}"))

    def test_a_relayed_source_is_never_reencoded(self) -> None:
        source = go2rtc_source("cam-1", "ws://h:1/", "h264")
        self.assertIn("-c:v copy", source)
        self.assertNotIn("libx264", source)


class CameraPayloadTests(unittest.TestCase):
    """Picking the demuxer for a raw camera bytestream."""

    def test_a_jpeg_start_of_image_is_mjpeg(self) -> None:
        self.assertEqual(detect_payload_format(b"\xff\xd8\xff\xe0rest"), "mjpeg")

    def test_three_and_four_byte_annexb_start_codes_are_h264(self) -> None:
        self.assertEqual(detect_payload_format(b"\x00\x00\x01\x67rest"), "h264")
        self.assertEqual(detect_payload_format(b"\x00\x00\x00\x01\x67x"), "h264")

    def test_an_unrecognized_payload_is_undecidable(self) -> None:
        self.assertIsNone(detect_payload_format(b"not video at all"))
        self.assertIsNone(detect_payload_format(b""))

    def test_only_non_empty_binary_messages_carry_video(self) -> None:
        self.assertTrue(should_forward(b"\x00\x00\x01\x67"))
        self.assertFalse(should_forward(b""))
        # The camera interleaves text status frames with the video payloads.
        self.assertFalse(should_forward('<playback_status status="ok" />'))


class CameraReconnectTests(unittest.TestCase):
    def test_backoff_doubles_and_is_capped(self) -> None:
        delays = [reconnect_delay(attempt) for attempt in range(1, 8)]
        self.assertEqual(delays, [1.0, 2.0, 4.0, 8.0, 15.0, 15.0, 15.0])

    def test_the_first_reconnect_is_not_instant(self) -> None:
        self.assertGreater(reconnect_delay(0), 0)


class RecordingsDirConfigTests(unittest.TestCase):
    """Where recordings are written, chosen by the operator in Settings."""

    def test_defaults_to_the_deployment_default(self) -> None:
        # Empty means "whatever RECORDINGS_DIR says", which is what the
        # container and the Steam launcher configure.
        self.assertEqual(validate_config({})[8], "")

    def test_an_absolute_path_is_accepted_and_trimmed(self) -> None:
        self.assertEqual(
            validate_config({"recordings_dir": "  /home/deck/Videos/runs  "})[8],
            "/home/deck/Videos/runs",
        )

    def test_an_empty_value_restores_the_default(self) -> None:
        self.assertEqual(validate_config({"recordings_dir": "   "})[8], "")

    def test_a_relative_path_is_rejected(self) -> None:
        # It would resolve against whatever directory the backend was started
        # from, which the operator cannot reason about.
        for bad in ("recordings", "./runs", "../runs", "~/Videos"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_config({"recordings_dir": bad})

    def test_a_non_string_is_rejected(self) -> None:
        for bad in (5, None, ["/tmp"], {"path": "/tmp"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_recordings_dir(bad)

    def test_adding_it_did_not_shift_the_existing_fields(self) -> None:
        # The tuple is indexed positionally throughout this suite, so a new
        # field must only ever be appended.
        config = validate_config({"camera_streams": [], "ptz_ip": "10.0.0.9"})
        self.assertEqual(config[3], ())
        self.assertEqual(config[4], "go2rtc")
        self.assertEqual(config[6], "10.0.0.9")
        self.assertIsInstance(config[7], dict)


class AnnexBTests(unittest.TestCase):
    """Reading an H.264 bytestream well enough to find its keyframes."""

    def test_nal_types_are_listed_in_order(self) -> None:
        payload = _nal(7) + _nal(8) + _nal(5)
        self.assertEqual(annexb_nal_types(payload), (7, 8, 5))

    def test_a_four_byte_start_code_is_the_same_code_behind_a_zero(self) -> None:
        self.assertEqual(annexb_nal_types(b"\x00" + _nal(5)), (5,))

    def test_a_payload_with_no_start_code_has_no_nals(self) -> None:
        self.assertEqual(annexb_nal_types(b"\xff\xd8\xff\xe0"), ())

    def test_a_truncated_start_code_at_the_end_is_not_a_nal(self) -> None:
        self.assertEqual(annexb_nal_types(_nal(1) + START_CODE), (1,))

    def test_parameter_sets_and_delimiters_carry_no_picture(self) -> None:
        for nal_type in (6, 7, 8, 9):
            with self.subTest(nal_type=nal_type):
                self.assertFalse(carries_picture(_nal(nal_type)))

    def test_slices_and_whole_jpegs_carry_a_picture(self) -> None:
        self.assertTrue(carries_picture(_nal(1)))
        self.assertTrue(carries_picture(_nal(5)))
        self.assertTrue(carries_picture(b"\xff\xd8\xff\xe0jpeg"))


class PrerollBufferTests(unittest.TestCase):
    """Holding the current GOP so a recording can start on a keyframe.

    Without it a stream copy has to wait for the camera's next keyframe --
    commonly ten seconds away on an IP camera -- and those seconds are missing
    from the front of the file.
    """

    def setUp(self) -> None:
        self.buffer = PrerollBuffer()

    def test_nothing_is_replayed_before_a_keyframe_arrives(self) -> None:
        # Joining a camera mid-GOP, there is nothing a decoder could use.
        self.buffer.add(_nal(1))
        self.buffer.add(_nal(1))
        self.assertEqual(self.buffer.snapshot(), ())

    def test_the_buffer_opens_at_a_keyframe_and_keeps_what_follows(self) -> None:
        keyframe = _nal(7) + _nal(8) + _nal(5)
        first, second = _nal(1), _nal(1) + _nal(1)
        for payload in (_nal(1), keyframe, first, second):
            self.buffer.add(payload)
        self.assertEqual(self.buffer.snapshot(), (keyframe, first, second))

    def test_parameter_sets_sent_ahead_of_their_keyframe_are_kept(self) -> None:
        # Some cameras send the SPS, the PPS and the IDR as three messages.
        # Restarting the buffer on the IDR would drop the parameter sets it
        # needs, and the recording would open on an undecodable frame.
        sps, pps, idr = _nal(7), _nal(8), _nal(5)
        for payload in (sps, pps, idr):
            self.buffer.add(payload)
        self.assertEqual(self.buffer.snapshot(), (sps, pps, idr))

    def test_each_new_keyframe_starts_the_buffer_over(self) -> None:
        older, newer = _nal(7) + _nal(5), _nal(7) + _nal(5)
        self.buffer.add(older)
        self.buffer.add(_nal(1))
        self.buffer.add(newer)
        self.assertEqual(self.buffer.snapshot(), (newer,))

    def test_a_bare_keyframe_starts_it_over_too(self) -> None:
        # A camera that sends its parameter sets only once still marks every
        # GOP with an IDR, and that is a fresh start.
        self.buffer.add(_nal(7) + _nal(5))
        self.buffer.add(_nal(1))
        idr = _nal(5)
        self.buffer.add(idr)
        self.assertEqual(self.buffer.snapshot(), (idr,))

    def test_only_the_latest_jpeg_is_held(self) -> None:
        # Every MJPEG frame stands alone, so the buffer is one frame deep.
        for index in range(3):
            self.buffer.add(b"\xff\xd8" + bytes([index]))
        self.assertEqual(self.buffer.snapshot(), (b"\xff\xd8\x02",))

    def test_a_reconnect_discards_the_buffer(self) -> None:
        self.buffer.add(_nal(7) + _nal(5))
        self.buffer.reset()
        self.assertEqual(self.buffer.snapshot(), ())

    def test_a_gop_that_outgrows_the_payload_cap_is_dropped_whole(self) -> None:
        # Trimming the front instead would leave a headless GOP, and splicing
        # a consumer into the middle of one corrupts it to the next keyframe.
        self.buffer.add(_nal(7) + _nal(5))
        for _ in range(PREROLL_MAX_PAYLOADS + 1):
            self.buffer.add(_nal(1))
        self.assertEqual(self.buffer.snapshot(), ())
        self.assertTrue(self.buffer.overflowed)

    def test_a_gop_that_outgrows_the_byte_cap_is_dropped_whole(self) -> None:
        self.buffer.add(_nal(7) + _nal(5))
        self.buffer.add(_nal(1, b"\x11" * PREROLL_MAX_BYTES))
        self.assertEqual(self.buffer.snapshot(), ())
        self.assertTrue(self.buffer.overflowed)

    def test_the_next_keyframe_recovers_from_an_overflow(self) -> None:
        self.buffer.add(_nal(7) + _nal(5))
        self.buffer.add(_nal(1, b"\x11" * PREROLL_MAX_BYTES))
        keyframe = _nal(7) + _nal(5)
        self.buffer.add(keyframe)
        self.assertEqual(self.buffer.snapshot(), (keyframe,))


class CameraHubPrerollTests(unittest.IsolatedAsyncioTestCase):
    """What each kind of subscriber is handed when it attaches."""

    async def asyncSetUp(self) -> None:
        self.hub = CameraWebSocketHub(logging.getLogger("test-camera-hub"))
        # Never dial the camera: these tests drive the fan-out directly.
        self.hub._run = lambda source: asyncio.sleep(0)
        await self.hub.update_streams((("cam", "ws://camera:1/stream"),))
        self.source = self.hub._sources["cam"]

    async def test_a_recorder_is_handed_the_buffered_gop_before_live_video(
        self,
    ) -> None:
        keyframe, held = _nal(7) + _nal(5), _nal(1)
        self.hub._publish(self.source, keyframe)
        self.hub._publish(self.source, held)

        async with self.hub.subscribe("cam", preroll=True) as stream:
            live = _nal(1) + _nal(1)
            self.hub._publish(self.source, live)
            received = [await stream.__anext__() for _ in range(3)]

        self.assertEqual(received, [keyframe, held, live])

    async def test_the_live_path_gets_only_what_arrives_after_it_attaches(
        self,
    ) -> None:
        self.hub._publish(self.source, _nal(7) + _nal(5))

        async with self.hub.subscribe("cam") as stream:
            live = _nal(1)
            self.hub._publish(self.source, live)
            self.assertEqual(await stream.__anext__(), live)

    async def test_a_prerolled_subscriber_keeps_its_whole_live_backlog(self) -> None:
        # The replay must not eat into the room a subscriber has to fall
        # behind in, or priming one would be enough to make it look slow.
        self.hub._publish(self.source, _nal(7) + _nal(5))
        for _ in range(20):
            self.hub._publish(self.source, _nal(1))

        async with self.hub.subscribe("cam", preroll=True) as stream:
            for _ in range(SUBSCRIBER_QUEUE_SIZE):
                self.hub._publish(self.source, _nal(1))
            subscriber = next(iter(self.source.subscribers))
            self.assertFalse(subscriber.overrun)
            self.assertEqual(await stream.__anext__(), _nal(7) + _nal(5))

    async def test_a_subscriber_that_attaches_mid_gop_waits_for_a_keyframe(
        self,
    ) -> None:
        # Nothing is buffered yet, so preroll can only be a no-op; the point
        # is that it never replays undecodable video.
        self.hub._publish(self.source, _nal(1))

        async with self.hub.subscribe("cam", preroll=True) as stream:
            live = _nal(7) + _nal(5)
            self.hub._publish(self.source, live)
            self.assertEqual(await stream.__anext__(), live)


class _FakePacket:
    """Enough of an av.Packet for the parameter-set check."""

    def __init__(self, data: bytes, keyframe: bool, extradata: bytes | None) -> None:
        self._data = data
        self.is_keyframe = keyframe
        codec = type("Codec", (), {"extradata": extradata})()
        self.stream = type("Stream", (), {"codec_context": codec})()

    def __bytes__(self) -> bytes:
        return self._data


class ParameterSetTests(unittest.TestCase):
    """Putting back what RTSP carries in the SDP instead of in the stream."""

    EXTRADATA = _nal(7) + _nal(8)

    def test_a_keyframe_without_parameter_sets_gets_them(self) -> None:
        packet = _FakePacket(_nal(5), keyframe=True, extradata=self.EXTRADATA)
        self.assertEqual(missing_parameter_sets(packet), self.EXTRADATA)

    def test_a_keyframe_that_already_carries_them_is_left_alone(self) -> None:
        packet = _FakePacket(
            self.EXTRADATA + _nal(5), keyframe=True, extradata=self.EXTRADATA
        )
        self.assertEqual(missing_parameter_sets(packet), b"")

    def test_nothing_is_prepended_to_a_predicted_frame(self) -> None:
        # They belong in front of a keyframe: a decoder that joins anywhere
        # else has nothing to apply them to.
        packet = _FakePacket(_nal(1), keyframe=False, extradata=self.EXTRADATA)
        self.assertEqual(missing_parameter_sets(packet), b"")

    def test_a_source_with_no_extradata_is_left_alone(self) -> None:
        packet = _FakePacket(_nal(5), keyframe=True, extradata=None)
        self.assertEqual(missing_parameter_sets(packet), b"")


class HubSourceOwnershipTests(unittest.IsolatedAsyncioTestCase):
    """Which sources the relay serves, and which are left to be dialed."""

    def setUp(self) -> None:
        self.hub = CameraWebSocketHub(logging.getLogger("test-camera-hub"))
        self.readers: list[str] = []
        self.hub.set_packet_source(lambda stream_id, url: self._reader(stream_id))

    def _reader(self, stream_id: str):
        async def reader() -> None:
            self.readers.append(stream_id)
            await asyncio.Event().wait()

        return reader

    async def test_go2rtc_leaves_rtsp_sources_to_be_dialed(self) -> None:
        # go2rtc holds the camera inside a child process, out of reach, so
        # there is no connection here for a recording to borrow.
        await self.hub.update_streams(
            (("rtsp-cam", "rtsp://h/s"), ("ws-cam", "ws://h:1/")), "go2rtc"
        )
        self.assertFalse(self.hub.has_stream("rtsp-cam"))
        self.assertTrue(self.hub.has_stream("ws-cam"))

    async def test_aiortc_serves_rtsp_sources_from_the_relay(self) -> None:
        await self.hub.update_streams((("rtsp-cam", "rtsp://h/s"),), "aiortc")
        self.assertTrue(self.hub.has_stream("rtsp-cam"))
        self.assertEqual(self.hub.input_format("rtsp-cam"), "h264")

    async def test_websocket_sources_are_held_whatever_the_backend(self) -> None:
        for backend in ("go2rtc", "aiortc"):
            with self.subTest(backend=backend):
                await self.hub.update_streams((("ws-cam", "ws://h:1/"),), backend)
                self.assertTrue(self.hub.has_stream("ws-cam"))

    async def test_a_backend_switch_drops_the_sources_it_no_longer_holds(self) -> None:
        await self.hub.update_streams((("rtsp-cam", "rtsp://h/s"),), "aiortc")
        await self.hub.update_streams((("rtsp-cam", "rtsp://h/s"),), "go2rtc")
        self.assertFalse(self.hub.has_stream("rtsp-cam"))

    async def test_a_pushed_payload_reaches_subscribers_and_the_buffer(self) -> None:
        # Whoever holds the camera publishes for as long as it holds it, so a
        # recording that starts later opens on a keyframe.
        await self.hub.update_streams((("rtsp-cam", "rtsp://h/s"),), "aiortc")
        keyframe = _nal(7) + _nal(5)
        self.hub.publish("rtsp-cam", keyframe)

        async with self.hub.subscribe("rtsp-cam", preroll=True) as stream:
            self.assertEqual(await stream.__anext__(), keyframe)

    async def test_publishing_to_an_unknown_stream_is_ignored(self) -> None:
        # A camera can outlive the config that named it by a moment.
        self.hub.publish("gone", _nal(5))



class _SilentTrack(MediaStreamTrack):
    """A camera that holds its connection open and sends nothing."""

    kind = "video"

    def __init__(self) -> None:
        super().__init__()
        self._ended = asyncio.Event()
        self.on("ended", self._ended.set)

    async def recv(self):
        await self._ended.wait()
        raise MediaStreamError


class _TickingTrack(MediaStreamTrack):
    """A camera delivering video as fast as the test needs it."""

    kind = "video"

    async def recv(self):
        await asyncio.sleep(0.005)
        return object()


class _FakePlayer:
    """Enough of a MediaPlayer to be held, drained and stopped."""

    def __init__(self, video: MediaStreamTrack) -> None:
        self.video = video
        self.audio = None


class CameraStallTests(unittest.IsolatedAsyncioTestCase):
    """A camera that stops sending without closing its connection.

    This is what an unplugged camera, a dropped link and a router that forgets
    a flow all look like from here, and none of them reaches any of the error
    paths: the player waits, the peer stays connected, and the last frame stays
    on screen until something times the silence.
    """

    async def asyncSetUp(self) -> None:
        self.backend = _AiortcStream(
            (("cam-0", "rtsp://camera/1"),), logging.getLogger("test-camera-stall")
        )
        self.shared = _SharedPlayer(
            "cam-0", "rtsp://camera/1", _FakePlayer(_SilentTrack()), decoded=True
        )
        self.shared.holders = 1
        self.backend._shared["cam-0"] = self.shared

    async def asyncTearDown(self) -> None:
        await _cancel_task(self.shared.tap)
        await _cancel_task(self.shared.watchdog)

    async def test_the_silence_is_timed_in_seconds(self) -> None:
        # Long enough to outlast a hiccup, short enough that the operator sees
        # the feed come back rather than wonders whether it ever will. The
        # exact value is the operator's to tune; a watchdog that fires in
        # milliseconds or in minutes would not be this feature.
        self.assertGreaterEqual(STALL_TIMEOUT_S, 1.0)
        self.assertLessEqual(STALL_TIMEOUT_S, 10.0)
        self.assertLess(STALL_CHECK_INTERVAL_S, STALL_TIMEOUT_S)

    async def test_the_watchdog_drops_a_source_that_stops_delivering(self) -> None:
        with mock.patch.object(camera_backend, "STALL_TIMEOUT_S", 0.05), \
                mock.patch.object(camera_backend, "STALL_CHECK_INTERVAL_S", 0.01):
            await asyncio.wait_for(self.backend._watch(self.shared), 2)

        # Gone from the held connections, so the next offer dials the camera
        # again instead of joining the one that stopped.
        self.assertNotIn("cam-0", self.backend._shared)
        self.assertEqual(self.shared.url, "")

    async def test_a_camera_that_keeps_sending_is_left_alone(self) -> None:
        loop = asyncio.get_running_loop()

        async def deliver() -> None:
            while True:
                self.shared.last_packet_at = loop.time()
                await asyncio.sleep(0.01)

        delivering = asyncio.create_task(deliver())
        with mock.patch.object(camera_backend, "STALL_TIMEOUT_S", 0.05), \
                mock.patch.object(camera_backend, "STALL_CHECK_INTERVAL_S", 0.01):
            watching = asyncio.create_task(self.backend._watch(self.shared))
            await asyncio.sleep(0.2)
            still_watching = not watching.done()
            await _cancel_task(watching)
        await _cancel_task(delivering)

        self.assertTrue(still_watching)
        self.assertIn("cam-0", self.backend._shared)

    async def test_the_tap_times_the_packets_it_drains(self) -> None:
        # The tap reads every source, not only the relayed ones: a connection
        # nobody pulls from cannot be told apart from one that has gone quiet.
        shared = _SharedPlayer(
            "cam-1", "rtsp://camera/2", _FakePlayer(_TickingTrack()), decoded=True
        )
        opened_at = shared.last_packet_at
        shared.tap = asyncio.create_task(self.backend._tap(shared))
        await asyncio.sleep(0.05)
        drained_at = shared.last_packet_at
        await _cancel_task(shared.tap)

        self.assertGreater(drained_at, opened_at)

    async def test_dropping_ends_the_source_the_relay_is_reading(self) -> None:
        # The recorder reads the connection the live view holds, and its retry
        # is driven by this event.
        self.shared.tap = asyncio.create_task(self.backend._tap(self.shared))
        await asyncio.sleep(0)
        await self.backend._drop(self.shared, "test")
        await asyncio.wait_for(self.shared.ended.wait(), 2)


class _WedgedTrack(MediaStreamTrack):
    """A player whose teardown waits on a camera that is not answering.

    aiortc stops a player by joining its worker thread, and that thread sits in
    demux() on the camera's socket until ffmpeg's read timeout expires.
    """

    kind = "video"

    def __init__(self) -> None:
        super().__init__()
        self.stops = 0

    async def recv(self):
        await asyncio.Event().wait()

    def stop(self) -> None:
        self.stops += 1
        time.sleep(0.3)


class CameraTeardownTests(unittest.IsolatedAsyncioTestCase):
    """Stopping a camera must not stop everything else with it."""

    async def test_the_loop_keeps_running_while_a_player_is_torn_down(self) -> None:
        # The regression this guards: the teardown ran on the event loop, so
        # dropping a stalled camera froze the 50 Hz command sender, telemetry,
        # PTZ and the control socket for as long as the camera took to let go.
        track = _WedgedTrack()
        shared = _SharedPlayer(
            "cam-0", "rtsp://camera/1", _FakePlayer(track), decoded=True
        )
        ticks = 0

        async def tick() -> None:
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        ticker = asyncio.create_task(tick())
        await shared.close()
        await _cancel_task(ticker)

        self.assertGreater(ticks, 5)

    async def test_every_holder_waits_on_one_teardown(self) -> None:
        # A peer and the recorder let go at the same moment; the camera must
        # not be told to close twice.
        track = _WedgedTrack()
        shared = _SharedPlayer(
            "cam-0", "rtsp://camera/1", _FakePlayer(track), decoded=True
        )

        await asyncio.gather(shared.close(), shared.close(), shared.close())

        self.assertEqual(track.stops, 1)


class CameraReconfigureTests(unittest.IsolatedAsyncioTestCase):
    """What a settings change costs the sources that are already connected."""

    def setUp(self) -> None:
        self.logger = logging.getLogger("test-camera-reconfigure")

    async def test_the_transport_does_not_rebuild_go2rtc(self) -> None:
        # rtsp_transport picks how *this* process dials RTSP. go2rtc dials in
        # a child process and never reads it, so rebuilding for it would stop,
        # start and re-dial every camera to change nothing.
        stream = WebRTCStream((), self.logger, backend="go2rtc")
        running = stream._stream

        self.assertFalse(await stream.update_config((), "go2rtc", "udp"))
        self.assertIs(stream._stream, running)
        # Still recorded, so switching to aiortc later dials the way the
        # operator asked.
        self.assertEqual(stream._rtsp_transport, "udp")

    async def test_the_transport_rebuilds_aiortc(self) -> None:
        stream = WebRTCStream((), self.logger, backend="aiortc")
        running = stream._stream

        self.assertTrue(await stream.update_config((), "aiortc", "udp"))
        self.assertIsNot(stream._stream, running)

    async def test_a_backend_change_rebuilds(self) -> None:
        stream = WebRTCStream((), self.logger, backend="go2rtc")

        self.assertTrue(await stream.update_config((), "aiortc", "tcp"))
        self.assertTrue(isinstance(stream._stream, _AiortcStream))

    async def test_an_unchanged_config_keeps_every_connection(self) -> None:
        stream = WebRTCStream((), self.logger, backend="aiortc")
        running = stream._stream

        self.assertFalse(await stream.update_config((), "aiortc", "tcp"))
        self.assertIs(stream._stream, running)


class CameraRestartNoticeTests(unittest.TestCase):
    """Which feeds the renderer is told to connect to again."""

    def test_an_edited_source_is_named_and_the_others_are_not(self) -> None:
        self.assertEqual(
            restarted_camera_streams(
                (("cam-0", "rtsp://a/1"), ("cam-1", "rtsp://b/1")),
                (("cam-0", "rtsp://a/2"), ("cam-1", "rtsp://b/1")),
                False,
            ),
            ("cam-0",),
        )

    def test_a_new_source_is_named(self) -> None:
        self.assertEqual(
            restarted_camera_streams((), (("cam-0", "rtsp://a/1"),), False),
            ("cam-0",),
        )

    def test_an_unchanged_config_names_nothing(self) -> None:
        streams = (("cam-0", "rtsp://a/1"), ("cam-1", "ws://b:1/"))
        self.assertEqual(restarted_camera_streams(streams, streams, False), ())

    def test_a_removed_source_names_nothing(self) -> None:
        # It has no connection to replace; the renderer drops the feed when the
        # stream leaves the config.
        self.assertEqual(
            restarted_camera_streams((("cam-0", "rtsp://a/1"),), (), False), ()
        )

    def test_a_backend_or_transport_change_names_every_source(self) -> None:
        # Both decide how a connection is opened, so every one is re-dialed.
        streams = (("cam-0", "rtsp://a/1"), ("cam-1", "ws://b:1/"))
        self.assertEqual(
            restarted_camera_streams(streams, streams, True), ("cam-0", "cam-1")
        )


if __name__ == "__main__":
    unittest.main()
