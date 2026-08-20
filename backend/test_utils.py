import struct
import unittest

import utils

SCHEMA = {
    "packet_types": {
        "send": {
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
        },
        "receive": {
            "byte_order": "little",
            "header": "ITS",
            "fields": [
                {
                    "name": "battery_level",
                    "type": "uint8",
                    "min": 0,
                    "max": 100,
                }
            ],
        },
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

    def test_unknown_and_out_of_range_fields_are_rejected(self) -> None:
        invalid_packets = (
            {"vy": 0.0, "mode": 2, "extra": 1},
            {"vy": 101.0, "mode": 2},
            {"vy": 0.0, "mode": -1},
        )

        for packet in invalid_packets:
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                utils.encode_binary_packet(packet, SCHEMA)

    def test_missing_fields_use_schema_defaults(self) -> None:
        payload = utils.encode_binary_packet({"vy": 0.0}, SCHEMA)

        self.assertEqual(len(payload), 9)
        self.assertEqual(payload[:3], b"ITS")
        self.assertEqual(struct.unpack("<fH", payload[3:]), (0.0, 2))

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

    def test_decoder_uses_named_schema_and_exact_layout(self) -> None:
        packet = utils.decode_binary_packet(b"ITS\x4b", SCHEMA, "receive")

        self.assertEqual(packet, {"battery_level": 75})

    def test_decoder_rejects_wrong_header_and_length(self) -> None:
        invalid_payloads = (b"BAD\x4b", b"ITS", b"ITS\x4b\x00")

        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                utils.decode_binary_packet(payload, SCHEMA, "receive")

    def test_decoder_applies_schema_bounds(self) -> None:
        with self.assertRaises(ValueError):
            utils.decode_binary_packet(b"ITS\xff", SCHEMA, "receive")

    def test_unknown_packet_type_and_wire_type_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            utils.decode_binary_packet(b"", SCHEMA, "missing")

        invalid_schema = {
            "packet_types": {
                "receive": {
                    "byte_order": "little",
                    "header": "ITS",
                    "fields": [{"name": "value", "type": "string"}],
                }
            }
        }
        with self.assertRaises(ValueError):
            utils.decode_binary_packet(b"ITS", invalid_schema, "receive")


if __name__ == "__main__":
    unittest.main()
