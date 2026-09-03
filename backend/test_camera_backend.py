import unittest

from PTZController import (
    direction_to_ptz_data,
    focus_to_focus_data_xml,
    normalize_direction,
    normalize_focus,
    normalize_zoom,
    zoom_to_ptz_data,
)
from server import validate_config
from WebRTCStream import _resolve_stream_id


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

    def test_rejects_non_rtsp_url(self) -> None:
        with self.assertRaises(ValueError):
            validate_config({"camera_streams": [{"id": "cam-0", "url": "http://x/y"}]})

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
        expected = {
            "left": "<pan>-60</pan><tilt>0</tilt>",
            "right": "<pan>60</pan><tilt>0</tilt>",
            "up": "<pan>0</pan><tilt>60</tilt>",
            "down": "<pan>0</pan><tilt>-60</tilt>",
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


if __name__ == "__main__":
    unittest.main()
