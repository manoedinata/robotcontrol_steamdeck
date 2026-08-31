import unittest

from PTZController import direction_to_ptz_data, normalize_direction
from server import validate_config


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


class PTZConfigTests(unittest.TestCase):
    def test_ptz_defaults_to_disabled(self) -> None:
        config = validate_config({})
        self.assertEqual(config[6], "")
        self.assertEqual(config[7], "")
        self.assertEqual(config[8], "")

    def test_ptz_ip_is_accepted_and_stripped(self) -> None:
        config = validate_config({"ptz_ip": "  192.168.1.64  "})
        self.assertEqual(config[6], "192.168.1.64")

    def test_unknown_ptz_field_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_config({"ptz_host": "192.168.1.64"})

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
        for value in ("spin", "", None, 3, "LEFT "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_direction(value)

    def test_normalize_direction_accepts_cardinals(self) -> None:
        for direction in ("left", "right", "up", "down"):
            with self.subTest(direction=direction):
                self.assertEqual(normalize_direction(direction), direction)


if __name__ == "__main__":
    unittest.main()
