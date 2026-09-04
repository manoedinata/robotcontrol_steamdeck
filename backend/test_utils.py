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

# A packet type may carry no header at all; its fields then start at offset 0.
HEADERLESS_SCHEMA = {
    "packet_types": {
        "send": {
            "byte_order": "little",
            "header": "",
            "fields": [{"name": "vy", "type": "float32", "default": 0.0}],
        },
        "receive": {
            "byte_order": "little",
            "fields": [{"name": "battery_level", "type": "uint8", "max": 100}],
        },
    }
}


# Ramp rates are optional per field; padding is never ramped.
SLEW_SCHEMA = {
    "packet_types": {
        "send": {
            "byte_order": "little",
            "header": "",
            "fields": [
                {"name": "vy", "type": "float32", "slew_rate": 250},
                {"name": "mode", "type": "uint16"},
                {"name": "spare", "type": "uint8", "role": "padding", "count": 4,
                 "slew_rate": 999},
            ],
        }
    }
}


class SlewProfileTests(unittest.TestCase):
    TICK = 0.02          # the 50 Hz UDP send loop

    def ramp(self, start, target, rate, ticks):
        """Run the limiter for `ticks` ticks, returning every value it took."""
        value = start
        values = []
        for _ in range(ticks):
            value = utils.slew_step(value, target, rate, self.TICK)
            values.append(value)
        return values

    def test_full_scale_step_ramps_instead_of_jumping(self) -> None:
        # 250 units/s at 50 Hz is 5 units per tick, so 0 -> 100 takes 20 ticks.
        values = self.ramp(0.0, 100.0, 250, 25)

        self.assertAlmostEqual(values[0], 5.0)
        self.assertAlmostEqual(values[19], 100.0)
        self.assertEqual(values[20:], [100.0] * 5)      # settles, no overshoot

    def test_no_tick_moves_further_than_the_rate_allows(self) -> None:
        previous = 0.0
        for value in self.ramp(0.0, 100.0, 250, 25):
            self.assertLessEqual(abs(value - previous), 250 * self.TICK + 1e-9)
            previous = value

    def test_the_ramp_is_symmetric_in_both_directions(self) -> None:
        up = self.ramp(0.0, 100.0, 250, 20)
        down = self.ramp(0.0, -100.0, 250, 20)

        self.assertEqual(up, [-value for value in down])

    def test_a_rate_of_zero_or_none_applies_the_target_at_once(self) -> None:
        for rate in (None, 0, 0.0):
            with self.subTest(rate=rate):
                self.assertEqual(utils.slew_step(0.0, 100.0, rate, self.TICK), 100.0)

    def test_no_elapsed_time_means_no_movement(self) -> None:
        # Distinct from having no rate: a tick where the clock did not advance
        # must not hand the target straight through.
        self.assertEqual(utils.slew_step(10.0, 100.0, 250, 0.0), 10.0)
        self.assertEqual(utils.slew_step(10.0, 100.0, 250, -1.0), 10.0)

    def test_a_sub_unit_step_still_advances_because_state_stays_float(self) -> None:
        # 10 units/s at 50 Hz is 0.2 per tick. Rounding the limiter's own state
        # would strand an integer field here; rounding only at encode time
        # lets it cross to 1 after five ticks.
        values = self.ramp(0.0, 100.0, 10, 5)

        self.assertAlmostEqual(values[-1], 1.0)
        self.assertEqual([round(value) for value in values], [0, 0, 1, 1, 1])

    def test_a_target_inside_one_step_lands_exactly_on_it(self) -> None:
        self.assertEqual(utils.slew_step(0.0, 1.5, 250, self.TICK), 1.5)
        self.assertEqual(utils.slew_step(100.0, 99.0, 250, self.TICK), 99.0)

    def test_rates_are_read_per_field_and_padding_is_skipped(self) -> None:
        self.assertEqual(utils.slew_rates(SLEW_SCHEMA), {"vy": 250.0})

    def test_invalid_rates_are_rejected(self) -> None:
        for rate in (-1, float("nan"), float("inf"), True, "fast"):
            with self.subTest(rate=rate):
                schema = {
                    "packet_types": {
                        "send": {
                            "byte_order": "little",
                            "fields": [
                                {"name": "vy", "type": "float32", "slew_rate": rate}
                            ],
                        }
                    }
                }
                with self.assertRaises(ValueError):
                    utils.slew_rates(schema)


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

    def test_empty_and_missing_headers_are_encoded_without_header_bytes(self) -> None:
        # "header": "" on send, no header key at all on receive.
        self.assertEqual(utils.packet_header(HEADERLESS_SCHEMA, "send"), b"")
        self.assertEqual(utils.packet_header(HEADERLESS_SCHEMA, "receive"), b"")

        payload = utils.encode_binary_packet({"vy": 1.5}, HEADERLESS_SCHEMA)
        self.assertEqual(payload, struct.pack("<f", 1.5))

    def test_headerless_packets_are_decoded_on_length_alone(self) -> None:
        packet = utils.decode_binary_packet(b"\x4b", HEADERLESS_SCHEMA, "receive")
        self.assertEqual(packet, {"battery_level": 75})

        for payload in (b"", b"\x4b\x00"):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                utils.decode_binary_packet(payload, HEADERLESS_SCHEMA, "receive")

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
