import asyncio
import logging
import os
import tempfile
import unittest
import Recorder as Recorder_module
from datetime import datetime
from pathlib import Path

from Recorder import (
    MIN_FREE_START_BYTES,
    PR_SET_PDEATHSIG,
    MIN_FREE_STOP_BYTES,
    Recorder,
    SourceRecording,
    backoff_delay,
    classify_exit,
    has_free_space,
    is_stalled,
    normalize_record_action,
    parse_ffmpeg_major,
    recording_path,
    recording_state_payload,
    relay_record_args,
    rtsp_record_args,
    rtsp_timeout_option,
    safe_component,
    scrub_credentials,
    seconds_remaining,
    session_dir_name,
)

ROOT = Path("/tmp/recordings")
TIMEOUT_OPTION = ("-stimeout", "5000000")


def input_options(args: tuple[str, ...]) -> tuple[str, ...]:
    """Everything before -i, which is the only place input options are read."""
    return args[: args.index("-i")]


class RecordActionTests(unittest.TestCase):
    def test_accepts_start_and_stop(self) -> None:
        self.assertEqual(normalize_record_action("start"), "start")
        self.assertEqual(normalize_record_action("stop"), "stop")

    def test_trims_and_lowercases(self) -> None:
        self.assertEqual(normalize_record_action("  START "), "start")

    def test_rejects_unknown_and_non_string_values(self) -> None:
        for bad in ("pause", "", None, 1, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_record_action(bad)


class SessionPathTests(unittest.TestCase):
    def test_session_name_is_filesystem_safe(self) -> None:
        name = session_dir_name(datetime(2026, 9, 9, 12, 30, 15))
        self.assertEqual(name, "20260909-123015")
        # exFAT, which the Deck's SD card usually is, rejects colons.
        self.assertNotIn(":", name)

    def test_session_names_sort_chronologically(self) -> None:
        earlier = session_dir_name(datetime(2026, 9, 9, 12, 30, 15))
        later = session_dir_name(datetime(2026, 9, 9, 12, 30, 16))
        next_day = session_dir_name(datetime(2026, 9, 10, 1, 0, 0))
        self.assertEqual(sorted([next_day, later, earlier]), [earlier, later, next_day])

    def test_part_numbers_are_zero_padded(self) -> None:
        path = recording_path(ROOT, "20260909-123015", "cam-0", 2)
        self.assertEqual(path.name, "cam-0_002.mkv")

    def test_a_long_session_still_produces_unique_names(self) -> None:
        first = recording_path(ROOT, "s", "cam-0", 999).name
        second = recording_path(ROOT, "s", "cam-0", 1000).name
        self.assertNotEqual(first, second)
        self.assertEqual(second, "cam-0_1000.mkv")

    def test_each_source_gets_its_own_file(self) -> None:
        one = recording_path(ROOT, "s", "cam-0", 1)
        two = recording_path(ROOT, "s", "cam-1", 1)
        self.assertNotEqual(one, two)
        self.assertEqual(one.parent, two.parent)

    def test_traversal_in_a_source_id_cannot_escape_the_root(self) -> None:
        path = recording_path(ROOT, "s", "../../etc/passwd", 1)
        self.assertTrue(path.is_relative_to(ROOT))
        self.assertNotIn("..", path.parts)

    def test_unsafe_components_are_rejected_outright(self) -> None:
        for bad in ("", ".", ".."):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                safe_component(bad)

    def test_a_resolved_path_stays_inside_the_root(self) -> None:
        path = recording_path(ROOT, "20260909-123015", "cam-0", 1)
        self.assertTrue(path.resolve().is_relative_to(ROOT.resolve()))


class FfmpegVersionTests(unittest.TestCase):
    def test_reads_the_major_from_a_banner(self) -> None:
        self.assertEqual(parse_ffmpeg_major("ffmpeg version 5.1.6-0+deb12u1"), 5)
        self.assertEqual(parse_ffmpeg_major("ffmpeg version 8.0.1-3ubuntu2"), 8)
        self.assertEqual(parse_ffmpeg_major("ffmpeg version n6.1.1"), 6)

    def test_an_unreadable_banner_falls_back_to_the_older_spelling(self) -> None:
        self.assertEqual(parse_ffmpeg_major("something else entirely"), 5)
        self.assertEqual(rtsp_timeout_option(parse_ffmpeg_major("")), ("-stimeout", "5000000"))

    def test_the_timeout_option_was_renamed_in_ffmpeg_6(self) -> None:
        self.assertEqual(rtsp_timeout_option(5)[0], "-stimeout")
        self.assertEqual(rtsp_timeout_option(6)[0], "-timeout")
        self.assertEqual(rtsp_timeout_option(8)[0], "-timeout")


class FfmpegArgumentTests(unittest.TestCase):
    def test_rtsp_is_never_reencoded_and_is_always_matroska(self) -> None:
        args = rtsp_record_args("rtsp://h/s", "/out/cam-0_001.mkv", TIMEOUT_OPTION)
        self.assertIn("-c", args)
        self.assertEqual(args[args.index("-c") + 1], "copy")
        self.assertEqual(args[args.index("-f", args.index("-i")) + 1], "matroska")
        self.assertEqual(args[-1], "/out/cam-0_001.mkv")

    def test_rtsp_records_over_tcp(self) -> None:
        # A lost RTP packet is permanent corruption in a stream copy, and
        # recording has no latency requirement, unlike the live path.
        args = rtsp_record_args("rtsp://h/s", "/out/a.mkv", TIMEOUT_OPTION)
        self.assertIn("-rtsp_transport", input_options(args))
        self.assertEqual(args[args.index("-rtsp_transport") + 1], "tcp")

    def test_rtsp_input_options_precede_the_input(self) -> None:
        args = rtsp_record_args("rtsp://h/s", "/out/a.mkv", TIMEOUT_OPTION)
        before = input_options(args)
        self.assertIn("-rtsp_transport", before)
        self.assertIn(TIMEOUT_OPTION[0], before)

    def test_rtsp_takes_audio_only_when_present_and_drops_data_streams(self) -> None:
        # "-map 0" picks up a Hikvision private data stream and fails the mux.
        args = rtsp_record_args("rtsp://h/s", "/out/a.mkv", TIMEOUT_OPTION)
        self.assertIn("0:v:0", args)
        self.assertIn("0:a?", args)
        self.assertIn("-dn", args)
        self.assertIn("-sn", args)

    def test_rtsp_does_not_override_the_cameras_own_timestamps(self) -> None:
        args = rtsp_record_args("rtsp://h/s", "/out/a.mkv", TIMEOUT_OPTION)
        self.assertNotIn("-use_wallclock_as_timestamps", args)

    def test_relay_names_the_demuxer_before_the_input(self) -> None:
        for fmt in ("h264", "mjpeg"):
            with self.subTest(fmt=fmt):
                args = relay_record_args(fmt, "http://127.0.0.1:8000/x", "/out/a.mkv")
                before = input_options(args)
                self.assertIn("-f", before)
                self.assertEqual(args[args.index("-f") + 1], fmt)

    def test_relay_stamps_packets_with_the_wall_clock(self) -> None:
        # A bare bytestream carries no timing, and ffmpeg otherwise assumes
        # 25 fps, so the file duration would not match real elapsed time.
        args = relay_record_args("h264", "http://127.0.0.1:8000/x", "/out/a.mkv")
        self.assertIn("-use_wallclock_as_timestamps", input_options(args))
        self.assertEqual(args[args.index("-use_wallclock_as_timestamps") + 1], "1")

    def test_relay_is_never_reencoded_and_is_always_matroska(self) -> None:
        args = relay_record_args("h264", "http://127.0.0.1:8000/x", "/out/a.mkv")
        self.assertEqual(args[args.index("-c") + 1], "copy")
        self.assertEqual(args[args.index("-f", args.index("-i")) + 1], "matroska")
        self.assertEqual(args[-1], "/out/a.mkv")

    def test_both_carry_a_dead_man_time_limit(self) -> None:
        # An ffmpeg orphaned by a killed backend must not hold the camera and
        # fill the disk forever.
        for args in (
            rtsp_record_args("rtsp://h/s", "/out/a.mkv", TIMEOUT_OPTION),
            relay_record_args("h264", "http://127.0.0.1:8000/x", "/out/a.mkv"),
        ):
            with self.subTest(args=args[0]):
                self.assertIn("-t", args)
                self.assertGreater(int(args[args.index("-t") + 1]), 0)

    def test_both_flush_output_so_the_file_size_tracks_reality(self) -> None:
        # Unflushed, ffmpeg holds output in its AVIO buffer and an on-disk size
        # of zero is indistinguishable from a wedged recording, so the stall
        # watchdog would kill healthy low-bitrate sources.
        for args in (
            rtsp_record_args("rtsp://h/s", "/out/a.mkv", TIMEOUT_OPTION),
            relay_record_args("h264", "http://127.0.0.1:8000/x", "/out/a.mkv"),
        ):
            with self.subTest(args=args[0]):
                self.assertIn("-flush_packets", args)
                self.assertEqual(args[args.index("-flush_packets") + 1], "1")

    def test_neither_reads_stdin(self) -> None:
        for args in (
            rtsp_record_args("rtsp://h/s", "/out/a.mkv", TIMEOUT_OPTION),
            relay_record_args("h264", "http://127.0.0.1:8000/x", "/out/a.mkv"),
        ):
            self.assertIn("-nostdin", args)


class SupervisionTests(unittest.TestCase):
    def test_backoff_doubles_and_is_capped(self) -> None:
        delays = [backoff_delay(attempt) for attempt in range(1, 8)]
        self.assertEqual(delays, [1.0, 2.0, 4.0, 8.0, 15.0, 15.0, 15.0])

    def test_a_long_run_that_dies_is_always_retried(self) -> None:
        # Losing a camera after a while is a network event, not a mistake.
        self.assertEqual(classify_exit(1, 600.0, 12), "retry")

    def test_three_immediate_failures_give_up_when_nothing_ever_recorded(self) -> None:
        # Never produced a byte: a wrong URL or bad credentials, not an outage.
        self.assertEqual(classify_exit(1, 0.2, 1), "retry")
        self.assertEqual(classify_exit(1, 0.2, 2), "retry")
        self.assertEqual(classify_exit(1, 0.2, 3), "give_up")

    def test_a_source_that_once_worked_is_retried_indefinitely(self) -> None:
        # A camera rebooting or a robot driving out of range refuses the dial
        # instantly, so fast repeated failures are exactly what an outage looks
        # like. Giving up would abandon the sources most worth waiting for.
        for attempt in (1, 3, 10, 100):
            with self.subTest(attempt=attempt):
                self.assertEqual(
                    classify_exit(1, 0.2, attempt, ever_recorded=True), "retry"
                )

    def test_a_file_that_stopped_growing_is_stalled(self) -> None:
        self.assertTrue(is_stalled(1000, 1000, 20.0))
        self.assertFalse(is_stalled(2000, 1000, 20.0))
        self.assertFalse(is_stalled(1000, 1000, 2.0))


class RedactionTests(unittest.TestCase):
    def test_userinfo_is_removed_from_an_ffmpeg_error(self) -> None:
        scrubbed = scrub_credentials(
            "rtsp://admin:hunter2@10.0.0.5:554/s: 401 Unauthorized"
        )
        self.assertNotIn("hunter2", scrubbed)
        self.assertNotIn("admin", scrubbed)
        self.assertIn("10.0.0.5", scrubbed)

    def test_multiple_urls_in_one_line_are_all_scrubbed(self) -> None:
        scrubbed = scrub_credentials("rtsp://a:b@h/1 failed over rtsp://c:d@h/2")
        self.assertNotIn("b@", scrubbed)
        self.assertNotIn("d@", scrubbed)

    def test_text_without_credentials_is_unchanged(self) -> None:
        for text in ("Connection refused", "rtsp://10.0.0.5/s: timeout"):
            self.assertEqual(scrub_credentials(text), text)


class DiskTests(unittest.TestCase):
    def test_free_space_predicate(self) -> None:
        self.assertTrue(has_free_space(MIN_FREE_START_BYTES, MIN_FREE_START_BYTES))
        self.assertFalse(has_free_space(MIN_FREE_START_BYTES - 1, MIN_FREE_START_BYTES))

    def test_the_stop_floor_is_below_the_start_floor(self) -> None:
        # Otherwise a recording would stop itself the moment it started.
        self.assertLess(MIN_FREE_STOP_BYTES, MIN_FREE_START_BYTES)

    def test_remaining_time_is_none_until_a_rate_is_measured(self) -> None:
        self.assertIsNone(seconds_remaining(10 * 1024**3, None))
        self.assertIsNone(seconds_remaining(10 * 1024**3, 0))

    def test_remaining_time_counts_down_to_the_stop_floor(self) -> None:
        free = MIN_FREE_STOP_BYTES + 1000
        self.assertEqual(seconds_remaining(free, 100), 10)

    def test_remaining_time_never_goes_negative(self) -> None:
        self.assertEqual(seconds_remaining(0, 100), 0)


class StatePayloadTests(unittest.TestCase):
    def build(self) -> dict:
        sources = [
            SourceRecording(
                source_id="cam-0",
                kind="rtsp",
                url="rtsp://admin:hunter2@10.0.0.5/s",
                status="recording",
                part=1,
                filename="cam-0_001.mkv",
                bytes_written=1234,
            )
        ]
        return recording_state_payload(
            active=True,
            session="20260909-123015",
            started_at=1757416215.0,
            directory="/app/recordings/20260909-123015",
            free_bytes=99,
            remaining=42.0,
            stopped_reason=None,
            sources=sources,
        )

    def test_payload_contains_one_entry_per_source(self) -> None:
        payload = self.build()
        self.assertEqual(payload["type"], "recording")
        self.assertTrue(payload["active"])
        self.assertEqual(len(payload["sources"]), 1)
        self.assertEqual(payload["sources"][0]["id"], "cam-0")
        self.assertEqual(payload["sources"][0]["file"], "cam-0_001.mkv")

    def test_payload_never_contains_a_camera_url(self) -> None:
        # These go to every connected UI and into the log.
        serialized = repr(self.build())
        self.assertNotIn("hunter2", serialized)
        self.assertNotIn("rtsp://", serialized)
        self.assertNotIn("url", self.build()["sources"][0])

    def test_a_lost_folder_is_reported_as_the_stop_reason(self) -> None:
        # Pulling the card mid-recording stops cleanly rather than leaving every
        # source retrying against a directory that no longer exists.
        payload = recording_state_payload(
            active=False, session=None, started_at=None, directory=None,
            free_bytes=None, remaining=None, stopped_reason="folder_lost",
            sources=[],
        )
        self.assertEqual(payload["stopped_reason"], "folder_lost")

    def test_idle_state_reports_no_session_and_no_sources(self) -> None:
        payload = recording_state_payload(
            active=False,
            session=None,
            started_at=None,
            directory=None,
            free_bytes=None,
            remaining=None,
            stopped_reason="operator",
            sources=[],
        )
        self.assertFalse(payload["active"])
        self.assertIsNone(payload["session_id"])
        self.assertEqual(payload["sources"], [])
        self.assertEqual(payload["stopped_reason"], "operator")


class ParentDeathTests(unittest.TestCase):
    """A killed backend must not leave recorders holding cameras open.

    die_with_parent() itself is deliberately not called here: it only makes
    sense between fork and exec, and in any other process its race check sees a
    parent that is not the recorded backend and exits immediately -- which is
    exactly what it should do in a child whose parent already died.
    """

    def test_the_prctl_option_number_is_the_linux_one(self) -> None:
        self.assertEqual(PR_SET_PDEATHSIG, 1)

    def test_the_recorded_backend_pid_is_this_process(self) -> None:
        # The hook compares against this rather than PID 1, so it stays correct
        # when the backend itself runs as the container's init process.
        self.assertEqual(Recorder_module._BACKEND_PID, os.getpid())


class RecordingsFolderTests(unittest.TestCase):
    """The folder is usually removable media, so it is never created."""

    def start(self, root: str) -> str:
        recorder = Recorder(logger=logging.getLogger("test"))
        recorder.set_root(root)
        recorder.update_sources((("cam-0", "rtsp://h/s"),))
        with self.assertRaises(ValueError) as caught:
            asyncio.run(recorder.start())
        return str(caught.exception)

    def test_a_missing_folder_refuses_to_record_and_is_not_created(self) -> None:
        # An absent card must read as "not available". Creating it would invent
        # a directory where the card is not -- in the container's ephemeral
        # filesystem, or under /run, which is tmpfs, where recording fills RAM.
        with tempfile.TemporaryDirectory() as parent:
            missing = os.path.join(parent, "card", "recordings")
            message = self.start(missing)
            self.assertIn("not available", message)
            self.assertFalse(os.path.exists(missing))
            self.assertFalse(os.path.exists(os.path.join(parent, "card")))

    def test_a_file_where_the_folder_should_be_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            path = os.path.join(parent, "not-a-dir")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("")
            self.assertIn("not available", self.start(path))

    def test_recording_with_no_sources_is_refused(self) -> None:
        recorder = Recorder(logger=logging.getLogger("test"))
        with tempfile.TemporaryDirectory() as root:
            recorder.set_root(root)
            with self.assertRaises(ValueError) as caught:
                asyncio.run(recorder.start())
        self.assertIn("no camera source", str(caught.exception))


class RecorderConstructionTests(unittest.TestCase):
    """server.py builds a Recorder at import time, so this must stay inert."""

    def test_a_fresh_recorder_is_idle_with_no_session(self) -> None:
        recorder = Recorder(logger=logging.getLogger("test"))
        self.assertFalse(recorder.active)
        state = recorder.state()
        self.assertFalse(state["active"])
        self.assertIsNone(state["session_id"])
        self.assertEqual(state["sources"], [])

    def test_constructing_one_resolves_no_paths_and_starts_nothing(self) -> None:
        recorder = Recorder(logger=logging.getLogger("test"))
        self.assertIsNone(recorder.state()["directory"])
        self.assertIsNone(recorder.state()["free_bytes"])

    def test_updating_sources_does_not_start_a_session(self) -> None:
        recorder = Recorder(logger=logging.getLogger("test"))
        recorder.update_sources((("cam-0", "rtsp://h/s"),))
        self.assertFalse(recorder.active)


if __name__ == "__main__":
    unittest.main()
