import unittest

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


if __name__ == "__main__":
    unittest.main()
