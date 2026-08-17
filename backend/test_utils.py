import struct
import unittest

import utils

SCHEMA = {
    "packet_types": {
        "command": {
            "byte_order": "little",
            "header": "ITS",
            "fields": [
                {
                    "name": "vy",
                    "type": "float32",
                    "default": 0.0,
                    "min": -100.0,
                    "max": 100.0,
                },
                {
                    "name": "mode",
                    "type": "uint16",
                    "default": 2,
                    "min": 0,
                    "max": 10,
                },
            ],
        }
    }
}


class BinaryPacketTests(unittest.TestCase):
    def test_defaults_follow_schema_fields(self) -> None:
        self.assertEqual(utils.generate_default_state(SCHEMA), {"vy": 0.0, "mode": 2})

    def test_encoder_uses_header_order_and_little_endian(self) -> None:
        payload = utils.encode_binary_packet({"vy": 1.5, "mode": 7}, SCHEMA)

        self.assertEqual(len(payload), 9)
        self.assertEqual(payload[:3], b"ITS")
        self.assertEqual(struct.unpack("<fH", payload[3:]), (1.5, 7))

    def test_unknown_missing_and_out_of_range_fields_are_rejected(self) -> None:
        invalid_packets = (
            {"vy": 0.0, "mode": 2, "extra": 1},
            {"vy": 0.0},
            {"vy": 101.0, "mode": 2},
            {"vy": 0.0, "mode": -1},
        )

        for packet in invalid_packets:
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                utils.encode_binary_packet(packet, SCHEMA)

    def test_integer_fields_reject_floats_and_booleans(self) -> None:
        for value in (1.25, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                utils.validate_packet_values({"mode": value}, SCHEMA)

    def test_float_fields_accept_json_integers_but_reject_non_finite_values(
        self,
    ) -> None:
        utils.validate_packet_values({"vy": 1}, SCHEMA)

        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                utils.validate_packet_values({"vy": value}, SCHEMA)


if __name__ == "__main__":
    unittest.main()
