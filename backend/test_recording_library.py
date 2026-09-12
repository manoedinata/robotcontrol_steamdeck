"""Reading recordings back: the manifest, the fallback, and the paths.

Every test here runs against fabricated files in a temporary directory or
against the pure functions directly. Nothing needs a camera, a backend server,
a browser, or real video.
"""

import asyncio
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path

import RecordingLibrary as Library
from Recorder import (
    MANIFEST_NAME,
    MANIFEST_TMP_NAME,
    MANIFEST_VERSION,
    Recorder,
    SourceRecording,
    manifest_payload,
    manifest_part,
    manifest_source,
    write_manifest,
)
from RecordingLibrary import FileEntry

LOGGER = logging.getLogger("test")


def entry(name: str, size: int = 1024, mtime_ns: int = 1_000_000_000) -> FileEntry:
    return FileEntry(name, size, mtime_ns)


class ManifestSchemaTests(unittest.TestCase):
    """What the recorder writes down about a session."""

    def payload(self, *sources: dict) -> dict:
        return manifest_payload(
            "20260101-101010", 100.0, 160.0, "complete", "operator", list(sources)
        )

    def test_a_source_that_recorded_nothing_is_still_listed(self) -> None:
        # The whole reason the manifest exists: a source that never received
        # video leaves no file, so the filesystem cannot distinguish it from a
        # source that was not part of the session at all.
        failed = SourceRecording(source_id="cam-1", kind="rtsp", url="rtsp://h/s")
        failed.status = "failed"
        failed.error = "no video was received"
        payload = self.payload(manifest_source(failed, []))
        self.assertEqual(payload["sources"][0]["id"], "cam-1")
        self.assertEqual(payload["sources"][0]["parts"], [])
        self.assertEqual(payload["sources"][0]["error"], "no video was received")

    def test_the_payload_never_contains_a_camera_url(self) -> None:
        state = SourceRecording(
            source_id="cam-0", kind="rtsp", url="rtsp://admin:hunter2@camera/stream"
        )
        state.stderr_tail.append("a line of ffmpeg output")
        text = json.dumps(self.payload(manifest_source(state, [])))
        self.assertNotIn("hunter2", text)
        self.assertNotIn("rtsp://", text)
        self.assertNotIn("camera", text)

    def test_the_payload_survives_a_json_round_trip(self) -> None:
        # Catches a deque or a Path leaking in from SourceRecording.
        state = SourceRecording(source_id="cam-0", kind="rtsp", url="rtsp://h/s")
        part = manifest_part("cam-0_001.mkv", 10, 1.5, 100.0, 101.5)
        payload = self.payload(manifest_source(state, [part]))
        self.assertEqual(json.loads(json.dumps(payload)), payload)

    def test_it_carries_a_version(self) -> None:
        self.assertEqual(self.payload()["version"], MANIFEST_VERSION)

    def test_a_session_in_progress_has_no_end(self) -> None:
        payload = manifest_payload("s", 100.0, None, "recording", None, [])
        self.assertEqual(payload["status"], "recording")
        self.assertIsNone(payload["ended_at"])
        self.assertIsNone(payload["stopped_reason"])


class ManifestWriteTests(unittest.TestCase):
    """Replacing the manifest is atomic and never raises."""

    def test_a_write_leaves_the_manifest_and_no_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.assertTrue(
                write_manifest(path, {"version": MANIFEST_VERSION}, LOGGER)
            )
            self.assertEqual([item.name for item in path.iterdir()], [MANIFEST_NAME])

    def test_a_second_write_replaces_the_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            write_manifest(path, manifest_payload("a", 1.0, None, "recording", None, []), LOGGER)
            write_manifest(path, manifest_payload("b", 2.0, 3.0, "complete", "operator", []), LOGGER)
            written = json.loads((path / MANIFEST_NAME).read_text())
            self.assertEqual(written["session_id"], "b")
            self.assertEqual(written["status"], "complete")

    @unittest.skipIf(os.geteuid() == 0, "root ignores the directory mode")
    def test_a_write_into_a_read_only_folder_fails_quietly(self) -> None:
        # A card that went read-only must cost a log line, not a recording.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            os.chmod(path, 0o555)
            try:
                self.assertFalse(write_manifest(path, {"version": 1}, LOGGER))
            finally:
                os.chmod(path, 0o755)


class EmptySessionTests(unittest.TestCase):
    """A session that produced no video leaves nothing behind."""

    def clean(self, contents: dict[str, bytes]) -> list[str]:
        with tempfile.TemporaryDirectory() as root:
            session = Path(root) / "20260101-101010"
            session.mkdir()
            for name, data in contents.items():
                (session / name).write_bytes(data)
            recorder = Recorder(logger=LOGGER)
            recorder._directory = str(session)
            recorder._remove_empty_session()
            if not session.is_dir():
                return []
            return sorted(item.name for item in session.iterdir())

    def test_a_folder_holding_only_a_manifest_is_removed(self) -> None:
        self.assertEqual(self.clean({MANIFEST_NAME: b"{}"}), [])

    def test_a_leftover_temporary_manifest_still_counts_as_empty(self) -> None:
        self.assertEqual(self.clean({MANIFEST_NAME: b"{}", MANIFEST_TMP_NAME: b"{"}), [])

    def test_a_folder_holding_video_is_kept_with_its_manifest(self) -> None:
        self.assertEqual(
            self.clean({MANIFEST_NAME: b"{}", "cam-0_001.mkv": b"video"}),
            sorted([MANIFEST_NAME, "cam-0_001.mkv"]),
        )


class PartNameTests(unittest.TestCase):
    """Which files in a session directory are recordings."""

    def test_a_part_name_splits_into_a_source_and_a_number(self) -> None:
        self.assertEqual(Library.parse_part_name("cam-0_003.mkv"), ("cam-0", 3))

    def test_a_source_id_may_contain_underscores(self) -> None:
        # Ids are [A-Za-z0-9_-], so the split has to be on the last underscore.
        self.assertEqual(Library.parse_part_name("front_cam_002.mkv"), ("front_cam", 2))

    def test_a_part_number_may_exceed_three_digits(self) -> None:
        self.assertEqual(Library.parse_part_name("cam-0_1000.mkv"), ("cam-0", 1000))

    def test_anything_else_is_not_a_recording(self) -> None:
        for name in (
            MANIFEST_NAME,
            MANIFEST_TMP_NAME,
            "notes.txt",
            "cam-0.mkv",
            "_001.mkv",
            "cam-0_abc.mkv",
            "cam-0_001.mp4",
        ):
            with self.subTest(name=name):
                self.assertIsNone(Library.parse_part_name(name))


class SessionNameTests(unittest.TestCase):
    def test_a_session_name_parses_back_to_its_local_time(self) -> None:
        from datetime import datetime

        expected = datetime(2026, 1, 2, 3, 4, 5).timestamp()
        self.assertEqual(Library.session_started_at("20260102-030405"), expected)

    def test_an_impossible_time_reads_as_unknown(self) -> None:
        self.assertIsNone(Library.session_started_at("20260103-303030"))


class ManifestReconciliationTests(unittest.TestCase):
    """The manifest describes the session; the disk corrects it."""

    def manifest(self, sources: list[dict], status: str = "complete") -> dict:
        return manifest_payload("20260101-101010", 100.0, 160.0, status, "operator", sources)

    def session(self, sources, files, active=False, status="complete") -> dict:
        return Library.session_from_manifest(
            "20260101-101010", self.manifest(sources, status), files, active
        )

    def test_bytes_come_from_disk_not_from_the_manifest(self) -> None:
        # A manifest written before a backend was killed records what it knew;
        # the file kept growing afterwards.
        source = {"id": "cam-0", "kind": "rtsp", "status": "stopped", "parts": [
            manifest_part("cam-0_001.mkv", 1024, 2.0, 100.0, 102.0)
        ]}
        session = self.session([source], [entry("cam-0_001.mkv", size=2048)])
        self.assertEqual(session["sources"][0]["parts"][0]["bytes"], 2048)
        self.assertEqual(session["bytes"], 2048)

    def test_a_source_with_no_parts_is_reported_as_not_recorded(self) -> None:
        source = {"id": "cam-1", "kind": "rtsp", "status": "failed",
                  "error": "no video was received", "parts": []}
        session = self.session([source], [])
        self.assertFalse(session["sources"][0]["recorded"])
        self.assertEqual(session["sources"][0]["error"], "no video was received")
        self.assertEqual(session["source_count"], 1)

    def test_a_part_the_manifest_never_mentioned_is_added(self) -> None:
        # The part being written when the backend was killed.
        source = {"id": "cam-0", "kind": "rtsp", "status": "recording", "parts": []}
        session = self.session([source], [entry("cam-0_001.mkv", size=77)], status="recording")
        parts = session["sources"][0]["parts"]
        self.assertEqual([part["file"] for part in parts], ["cam-0_001.mkv"])
        self.assertIsNone(parts[0]["duration"])
        self.assertEqual(parts[0]["bytes"], 77)

    def test_a_part_deleted_by_hand_is_dropped_but_its_source_stays(self) -> None:
        source = {"id": "cam-0", "kind": "rtsp", "status": "stopped", "parts": [
            manifest_part("cam-0_001.mkv", 10, 1.0, 100.0, 101.0)
        ]}
        session = self.session([source], [])
        self.assertEqual(session["sources"][0]["parts"], [])
        self.assertFalse(session["sources"][0]["recorded"])

    def test_a_file_belonging_to_no_listed_source_is_still_reported(self) -> None:
        session = self.session([], [entry("cam-7_001.mkv")])
        self.assertEqual([source["id"] for source in session["sources"]], ["cam-7"])

    def test_a_source_duration_is_the_sum_of_its_parts(self) -> None:
        source = {"id": "cam-0", "kind": "rtsp", "status": "stopped", "parts": [
            manifest_part("cam-0_001.mkv", 10, 1.5, 100.0, 101.5),
            manifest_part("cam-0_002.mkv", 10, 2.25, 102.0, 104.25),
        ]}
        session = self.session([source], [entry("cam-0_001.mkv"), entry("cam-0_002.mkv")])
        self.assertEqual(session["sources"][0]["duration"], 3.75)

    def test_a_source_duration_is_unknown_while_any_part_is(self) -> None:
        # A partial sum would read as a short recording rather than as an
        # incomplete measurement.
        source = {"id": "cam-0", "kind": "rtsp", "status": "stopped", "parts": [
            manifest_part("cam-0_001.mkv", 10, 1.5, 100.0, 101.5)
        ]}
        session = self.session([source], [entry("cam-0_001.mkv"), entry("cam-0_002.mkv")])
        self.assertIsNone(session["sources"][0]["duration"])

    def test_a_session_the_recorder_is_not_running_reads_as_interrupted(self) -> None:
        source = {"id": "cam-0", "kind": "rtsp", "status": "recording", "parts": []}
        session = self.session([source], [], active=False, status="recording")
        self.assertEqual(session["status"], "interrupted")
        self.assertEqual(session["sources"][0]["status"], "interrupted")

    def test_the_running_session_reads_as_recording(self) -> None:
        source = {"id": "cam-0", "kind": "rtsp", "status": "recording", "parts": []}
        session = self.session([source], [], active=True, status="recording")
        self.assertEqual(session["status"], "recording")
        self.assertTrue(session["active"])
        self.assertEqual(session["sources"][0]["status"], "recording")


class FilenameFallbackTests(unittest.TestCase):
    """A folder with no usable manifest is still listed."""

    def test_sources_are_grouped_by_the_id_in_the_filenames(self) -> None:
        session = Library.session_from_files(
            "20260101-101010",
            [entry("cam-0_001.mkv", 10), entry("cam-0_002.mkv", 20), entry("cam-1_001.mkv", 30)],
            False,
        )
        self.assertEqual([source["id"] for source in session["sources"]], ["cam-0", "cam-1"])
        self.assertEqual(session["sources"][0]["part_count"], 2)
        self.assertEqual(session["bytes"], 60)

    def test_what_cannot_be_known_reads_as_unknown_not_as_an_error(self) -> None:
        session = Library.session_from_files("20260101-101010", [entry("cam-0_001.mkv")], False)
        self.assertFalse(session["manifest"])
        self.assertEqual(session["status"], "scanned")
        self.assertEqual(session["sources"][0]["status"], "unknown")
        self.assertIsNone(session["sources"][0]["kind"])
        self.assertIsNone(session["sources"][0]["restarts"])

    def test_a_scanned_session_claims_no_duration(self) -> None:
        # ended_at is the newest file's mtime, which says when the folder was
        # last touched: a file restored years later must not read as a
        # recording that ran for years.
        session = Library.session_from_files(
            "20260101-101010", [entry("cam-0_001.mkv", mtime_ns=9_000_000_000_000)], False
        )
        self.assertIsNotNone(session["ended_at"])
        self.assertIsNone(session["duration"])

    def test_an_empty_folder_is_reported_rather_than_skipped(self) -> None:
        session = Library.session_from_files("20260101-101010", [], False)
        self.assertEqual(session["source_count"], 0)
        self.assertEqual(session["bytes"], 0)


class ManifestValidationTests(unittest.TestCase):
    def test_an_unknown_version_is_not_used(self) -> None:
        self.assertIsNone(Library.valid_manifest({"version": 99, "sources": []}))

    def test_a_manifest_without_sources_is_not_used(self) -> None:
        self.assertIsNone(Library.valid_manifest({"version": MANIFEST_VERSION}))

    def test_a_non_object_is_not_used(self) -> None:
        self.assertIsNone(Library.valid_manifest([1, 2, 3]))


class ScanTests(unittest.TestCase):
    """Walking a real directory."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def session(self, name: str, files: dict[str, bytes], manifest: str | None = None) -> Path:
        directory = self.root / name
        directory.mkdir()
        for filename, data in files.items():
            (directory / filename).write_bytes(data)
        if manifest is not None:
            (directory / MANIFEST_NAME).write_text(manifest)
        return directory

    def test_a_missing_root_is_an_empty_library_not_an_error(self) -> None:
        library = Library.scan_library(self.root / "gone", None)
        self.assertFalse(library["available"])
        self.assertEqual(library["sessions"], [])
        self.assertIsNone(library["free_bytes"])
        self.assertIsInstance(library["root"], str)

    def test_sessions_come_back_newest_first(self) -> None:
        for name in ("20260101-101010", "20260301-101010", "20260201-101010"):
            self.session(name, {"cam-0_001.mkv": b"x"})
        library = Library.scan_library(self.root, None)
        self.assertEqual(
            [session["id"] for session in library["sessions"]],
            ["20260301-101010", "20260201-101010", "20260101-101010"],
        )

    def test_the_listing_carries_no_per_file_detail(self) -> None:
        self.session("20260101-101010", {"cam-0_001.mkv": b"x"})
        library = Library.scan_library(self.root, None)
        self.assertNotIn("parts", library["sessions"][0]["sources"][0])

    def test_a_corrupt_manifest_falls_back_to_the_filenames(self) -> None:
        self.session("20260101-101010", {"cam-0_001.mkv": b"x"}, manifest='{"version": 1, "sources": [')
        library = Library.scan_library(self.root, None)
        session = library["sessions"][0]
        self.assertFalse(session["manifest"])
        self.assertEqual(session["sources"][0]["id"], "cam-0")

    def test_the_manifest_is_never_counted_as_a_recording(self) -> None:
        self.session("20260101-101010", {}, manifest='{"version": 1, "sources": []}')
        library = Library.scan_library(self.root, None)
        self.assertEqual(library["sessions"][0]["part_count"], 0)

    def test_a_plain_file_in_the_root_is_not_a_session(self) -> None:
        (self.root / "20260101-101010").write_text("not a directory")
        self.assertEqual(Library.scan_library(self.root, None)["sessions"], [])

    def test_a_directory_that_is_not_a_session_name_is_ignored(self) -> None:
        (self.root / "scratch").mkdir()
        self.assertEqual(Library.scan_library(self.root, None)["sessions"], [])

    def test_a_symlinked_session_is_not_listed(self) -> None:
        # session_path refuses to resolve one, so listing it would offer a
        # session nothing else can open.
        target = self.session("20260101-101010", {"cam-0_001.mkv": b"x"})
        os.symlink(target, self.root / "20260202-202020")
        library = Library.scan_library(self.root, None)
        self.assertEqual([session["id"] for session in library["sessions"]], ["20260101-101010"])

    def test_the_running_session_is_flagged(self) -> None:
        self.session("20260101-101010", {"cam-0_001.mkv": b"x"})
        library = Library.scan_library(self.root, "20260101-101010")
        self.assertTrue(library["sessions"][0]["active"])
        self.assertEqual(library["active_session_id"], "20260101-101010")

    def test_the_library_is_an_object_not_a_bare_array(self) -> None:
        library = Library.scan_library(self.root, None)
        self.assertIsInstance(library, dict)
        self.assertIn("sessions", library)


class ContainmentTests(unittest.TestCase):
    """Operator-supplied names cannot reach outside the recordings folder."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_traversal_in_a_session_name_stays_inside_the_root(self) -> None:
        resolved = Library.session_path(self.root, "../../etc")
        self.assertTrue(resolved.is_relative_to(self.root.resolve()))

    def test_a_bare_dot_or_empty_name_is_refused(self) -> None:
        for name in ("", ".", ".."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                Library.session_path(self.root, name)

    def test_the_root_itself_is_not_a_session(self) -> None:
        with self.assertRaises(ValueError):
            Library.session_path(self.root, ".")

    def test_a_symlink_out_of_the_root_is_refused(self) -> None:
        os.symlink("/etc", self.root / "20260101-101010")
        with self.assertRaises(ValueError):
            Library.session_path(self.root, "20260101-101010")

    def test_only_a_recording_file_can_be_addressed(self) -> None:
        for name in ("../../etc/passwd", MANIFEST_NAME, "notes.txt", "cam-0.mkv"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                Library.part_path(self.root, "20260101-101010", name)

    def test_a_part_resolves_inside_its_session(self) -> None:
        resolved = Library.part_path(self.root, "20260101-101010", "cam-0_001.mkv")
        self.assertEqual(resolved.parent.name, "20260101-101010")
        self.assertTrue(resolved.is_relative_to(self.root.resolve()))


class RemoveSessionTests(unittest.TestCase):
    def test_removing_a_session_reports_what_it_held(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            session = Path(root) / "20260101-101010"
            session.mkdir()
            (session / "cam-0_001.mkv").write_bytes(b"x" * 500)
            (session / MANIFEST_NAME).write_text("{}")
            freed, free_bytes = Library.remove_session(session)
            self.assertFalse(session.exists())
            self.assertEqual(freed, 502)
            self.assertIsInstance(free_bytes, int)


class SeekAndWidthTests(unittest.TestCase):
    def test_a_seek_offset_must_be_a_finite_non_negative_number(self) -> None:
        self.assertEqual(Library.normalize_seek("12.5"), 12.5)
        for bad in ("-1", "abc", "nan", "inf", None):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                Library.normalize_seek(bad)

    def test_a_thumbnail_width_must_be_one_of_the_offered_sizes(self) -> None:
        self.assertEqual(Library.normalize_thumbnail_width(None), Library.THUMBNAIL_WIDTH)
        self.assertEqual(Library.normalize_thumbnail_width("640"), 640)
        for bad in ("999", "0", "abc"):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                Library.normalize_thumbnail_width(bad)


def input_options(args: tuple[str, ...]) -> tuple[str, ...]:
    return args[: args.index("-i")]


class RemuxArgumentTests(unittest.TestCase):
    """The playback command, which must not re-encode unless it has to."""

    def test_it_produces_fragmented_mp4_on_a_pipe(self) -> None:
        args = Library.remux_args("/recordings/s/cam-0_001.mkv")
        self.assertIn("-movflags", args)
        flags = args[args.index("-movflags") + 1]
        for fragment in ("+frag_keyframe", "empty_moov", "default_base_moof"):
            self.assertIn(fragment, flags)
        self.assertEqual(args[-3:], ("-f", "mp4", "pipe:1"))

    def test_nothing_is_re_encoded_by_default(self) -> None:
        args = Library.remux_args("/x.mkv", audio_codec="aac")
        self.assertEqual(args[args.index("-c:v") + 1], "copy")
        self.assertNotIn("libx264", args)

    def test_a_seek_is_an_input_option(self) -> None:
        # Before -i, so ffmpeg seeks by index instead of decoding from the
        # start of the file.
        args = Library.remux_args("/x.mkv", start=12.0)
        self.assertIn("-ss", input_options(args))

    def test_no_seek_is_asked_for_at_the_beginning(self) -> None:
        self.assertNotIn("-ss", Library.remux_args("/x.mkv", start=0.0))

    def test_the_transcode_variant_is_only_used_when_asked(self) -> None:
        args = Library.remux_args("/x.mkv", transcode=True)
        self.assertIn("libx264", args)
        self.assertIn("yuv420p", args)

    def test_audio_mp4_cannot_carry_is_re_encoded(self) -> None:
        # A muxer error would empty the whole response, so only codecs MP4
        # takes as-is are copied.
        self.assertEqual(self.audio_codec("aac"), "copy")
        self.assertEqual(self.audio_codec("pcm_alaw"), "aac")
        self.assertEqual(self.audio_codec(None), "aac")

    def audio_codec(self, recorded: str | None) -> str:
        args = Library.remux_args("/x.mkv", audio_codec=recorded)
        return args[args.index("-c:a") + 1]

    def test_it_does_not_read_the_terminal(self) -> None:
        self.assertIn("-nostdin", Library.remux_args("/x.mkv"))

    def test_it_carries_a_dead_man_limit(self) -> None:
        args = Library.remux_args("/x.mkv")
        self.assertGreater(int(args[args.index("-t") + 1]), 0)


class ThumbnailArgumentTests(unittest.TestCase):
    def test_it_writes_one_frame_to_a_pipe(self) -> None:
        args = Library.thumbnail_args("/x.mkv", 1.0, 320)
        self.assertIn("-ss", input_options(args))
        self.assertEqual(args[args.index("-frames:v") + 1], "1")
        self.assertEqual(args[-3:], ("-f", "mjpeg", "pipe:1"))

    def test_no_output_file_is_named(self) -> None:
        # Nothing is written into the recordings folder on a read.
        args = Library.thumbnail_args("/recordings/s/cam-0_001.mkv", 1.0, 320)
        self.assertEqual([item for item in args if item.endswith(".jpg")], [])

    def test_the_offset_lands_inside_a_short_clip(self) -> None:
        self.assertEqual(Library.thumbnail_offset(0.4), 0.2)
        self.assertEqual(Library.thumbnail_offset(30.0), 1.0)
        self.assertEqual(Library.thumbnail_offset(None), 1.0)

    def test_the_cache_key_follows_the_file_and_the_size(self) -> None:
        path = Path("/recordings/s/cam-0_001.mkv")
        base = Library.thumbnail_cache_key(path, 10, 20, 320)
        self.assertEqual(base, Library.thumbnail_cache_key(path, 10, 20, 320))
        self.assertNotEqual(base, Library.thumbnail_cache_key(path, 11, 20, 320))
        self.assertNotEqual(base, Library.thumbnail_cache_key(path, 10, 21, 320))
        self.assertNotEqual(base, Library.thumbnail_cache_key(path, 10, 20, 640))

    def test_the_cache_key_is_a_safe_filename(self) -> None:
        key = Library.thumbnail_cache_key(Path("/a/b.mkv"), 1, 2, 320)
        self.assertTrue(all(character in "0123456789abcdef" for character in key))

    def test_the_cache_is_never_inside_the_recordings_folder(self) -> None:
        self.assertFalse(str(Library.thumbnail_cache_dir()).startswith("/run/media"))


class ProbeParsingTests(unittest.TestCase):
    """ffprobe output, parsed without running ffprobe."""

    def test_a_normal_file_yields_its_duration_and_codec(self) -> None:
        measured = Library.parse_probe_output(json.dumps({
            "format": {"duration": "12.500000"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }))
        self.assertEqual(measured["duration"], 12.5)
        self.assertEqual(measured["codec"], "h264")
        self.assertEqual(measured["width"], 1920)
        self.assertEqual(measured["audio_codec"], "aac")

    def test_an_unknown_duration_reads_as_none(self) -> None:
        measured = Library.parse_probe_output(json.dumps({"format": {"duration": "N/A"}}))
        self.assertIsNone(measured["duration"])

    def test_unparseable_output_is_not_an_error(self) -> None:
        self.assertIsNone(Library.parse_probe_output("not json at all")["codec"])

    def test_only_a_codec_the_renderer_decodes_is_playable(self) -> None:
        self.assertTrue(Library.is_browser_playable("h264"))
        self.assertTrue(Library.is_browser_playable("hevc"))
        self.assertFalse(Library.is_browser_playable("mjpeg"))
        self.assertFalse(Library.is_browser_playable(None))


class RemuxSlotTests(unittest.TestCase):
    """Playback is capped before the response starts, not inside it."""

    def tearDown(self) -> None:
        while Library._active_remuxes:
            Library.release_remux_slot()

    def test_the_cap_refuses_rather_than_queues(self) -> None:
        for _ in range(Library.REMUX_MAX_CONCURRENT):
            self.assertTrue(Library.acquire_remux_slot())
        self.assertFalse(Library.acquire_remux_slot())
        Library.release_remux_slot()
        self.assertTrue(Library.acquire_remux_slot())


class RecorderAccessorTests(unittest.TestCase):
    """What the endpoints ask the recorder for."""

    def test_the_root_is_resolved_per_call_not_at_construction(self) -> None:
        recorder = Recorder(logger=LOGGER)
        recorder.set_root("/somewhere/recordings")
        self.assertEqual(recorder.recordings_root(), Path("/somewhere/recordings"))

    def test_an_idle_recorder_has_no_active_session(self) -> None:
        self.assertIsNone(Recorder(logger=LOGGER).active_session_id())


class FillDetailTests(unittest.IsolatedAsyncioTestCase):
    """Probing an opened session, with the probe stubbed out."""

    async def test_probing_fills_the_durations_a_scan_could_not(self) -> None:
        session = Library.session_from_files(
            "20260101-101010", [entry("cam-0_001.mkv"), entry("cam-0_002.mkv")], False
        )
        self.assertIsNone(session["sources"][0]["duration"])

        async def fake_probe(path, logger):
            return {"duration": 2.5, "codec": "h264", "width": 640, "height": 480,
                    "audio_codec": None}

        original = Library.probe_part
        Library.probe_part = fake_probe
        try:
            await Library.fill_part_details(session, Path("/nowhere"), LOGGER)
        finally:
            Library.probe_part = original

        self.assertEqual(session["sources"][0]["duration"], 5.0)
        self.assertTrue(session["sources"][0]["parts"][0]["playable"])
        self.assertEqual(session["sources"][0]["parts"][0]["codec"], "h264")


if __name__ == "__main__":
    unittest.main()
