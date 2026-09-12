"""Read recordings back: list past sessions, play one, delete one.

The recorder writes ``<root>/<YYYYmmdd-HHMMSS>/<source id>_<NNN>.mkv`` and a
``session.json`` manifest beside them. This module is the other direction, and
it is deliberately one-way: it reads the recordings folder and never writes to
it. The only exception is ``DELETE /recordings/<session>``, which the operator
asks for explicitly. Caches -- thumbnails, probe results -- live elsewhere,
because that folder is usually a card that may be read-only, nearly full, or
about to be pulled, and because the operator opens it in a file manager.

A folder without a manifest is still listed. Everything recoverable is
recovered from the directory name and the filenames, and the rest reads as
unknown rather than as an error: that path covers recordings made before the
manifest existed, and the last part of a session whose backend was killed.

Imports run one way, ``RecordingLibrary`` -> ``Recorder``, so the writer never
depends on the reader.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from collections import OrderedDict
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, NamedTuple

from Recorder import (
    MANIFEST_NAME,
    MANIFEST_VERSION,
    MAX_SESSION_S,
    die_with_parent,
    safe_component,
    scrub_credentials,
)

# One recorded file. A session directory holds nothing else the library cares
# about, so every part name must parse: the part number is what orders them,
# and the prefix is which camera wrote them.
PART_SUFFIX = ".mkv"
PART_NAME_RE = re.compile(r"^(?P<id>.+)_(?P<part>\d{3,})\.mkv$")

# Session directories are named by the recorder as local time without colons,
# which the Deck's exFAT cards forbid. The format sorts chronologically, so
# "newest first" is a reverse sort on the name and needs no stat.
SESSION_NAME_RE = re.compile(r"^\d{8}-\d{6}$")
SESSION_NAME_FORMAT = "%Y%m%d-%H%M%S"

# What a <video> element in Electron's Chromium will actually decode once the
# part is remuxed into MP4. An MJPEG recording is the reason this list exists:
# MP4 can carry it, and Chromium renders it as nothing at all, which is a worse
# failure than refusing to play it.
BROWSER_PLAYABLE_CODECS = frozenset({"h264", "hevc", "h265", "av1"})
# Audio MP4 takes as-is. Anything else -- G.711 from a cheap camera, say -- is
# re-encoded, because a muxer error would empty the whole response.
MP4_AUDIO_COPY_CODECS = frozenset({"aac", "mp3", "ac3", "eac3", "alac"})

PROBE_TIMEOUT_S = 2.0
PROBE_CONCURRENCY = 4
PROBE_CACHE_MAX = 512

THUMBNAIL_TIMEOUT_S = 5.0
THUMBNAIL_WIDTH = 320
THUMBNAIL_WIDTHS = (160, 320, 640)
THUMBNAIL_QUALITY = "5"
THUMBNAIL_CACHE_MAX = 512
THUMBNAIL_CACHE_ENV = "RECORDING_THUMBNAIL_DIR"

# A remux is one ffmpeg per viewer. Two at once is already more than a single
# operator needs, and the Deck is assumed to be doing something else.
REMUX_MAX_CONCURRENT = 2
REMUX_CHUNK_BYTES = 64 * 1024
REMUX_STDERR_TAIL = 10


class FileEntry(NamedTuple):
    """One file in a session directory, as the scan found it."""

    name: str
    size: int
    mtime_ns: int


# --------------------------------------------------------------------------
# Names and paths
# --------------------------------------------------------------------------


def parse_part_name(name: str) -> tuple[str, int] | None:
    """Split ``cam-0_003.mkv`` into its source id and part number.

    Source ids may contain underscores, so the split is on the last one. A name
    that does not parse is not a recording: ``session.json`` and anything the
    operator dropped in the folder fall out here.
    """
    match = PART_NAME_RE.match(name)
    if match is None:
        return None
    return match.group("id"), int(match.group("part"))


def session_started_at(session_id: str) -> float | None:
    """The wall-clock time a session directory name encodes, as local time."""
    try:
        return datetime.strptime(session_id, SESSION_NAME_FORMAT).timestamp()
    except ValueError:
        return None


def session_path(root: Path | str, session: str) -> Path:
    """Where one session directory is, refusing anything outside the root.

    ``safe_component`` rewrites rather than rejects, so ``../../etc`` becomes
    one flat name that cannot exist; the containment check is defence in depth,
    and it is what catches a symlink inside the root pointing out of it --
    which matters here, because this path is handed to ``rmtree``.
    """
    root = Path(root)
    resolved = (root / safe_component(session)).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("recording path escapes the recordings directory")
    if resolved == root.resolve():
        raise ValueError("not a recording session")
    return resolved


def part_path(root: Path | str, session: str, filename: str) -> Path:
    """Where one recorded file is, refusing anything that is not one."""
    if parse_part_name(filename) is None:
        raise ValueError("not a recording file")
    directory = session_path(root, session)
    resolved = (directory / safe_component(filename)).resolve()
    if not resolved.is_relative_to(directory):
        raise ValueError("recording path escapes the recordings directory")
    return resolved


def normalize_seek(value: Any) -> float:
    """Validate a ``?t=`` playback offset in seconds."""
    try:
        seconds = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("seek offset must be a number") from error
    if seconds != seconds or seconds in (float("inf"), float("-inf")):
        raise ValueError("seek offset must be finite")
    if seconds < 0:
        raise ValueError("seek offset must not be negative")
    return seconds


def normalize_thumbnail_width(value: Any) -> int:
    """Validate a ``?w=`` thumbnail width.

    A fixed set rather than a range: the parameter must not become a way to ask
    the backend to render arbitrary sizes, each of which is its own ffmpeg and
    its own cache entry.
    """
    if value is None:
        return THUMBNAIL_WIDTH
    try:
        width = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("thumbnail width must be a number") from error
    if width not in THUMBNAIL_WIDTHS:
        allowed = ", ".join(str(option) for option in THUMBNAIL_WIDTHS)
        raise ValueError(f"thumbnail width must be one of {allowed}")
    return width


def is_browser_playable(codec: str | None) -> bool:
    return codec is not None and codec.lower() in BROWSER_PLAYABLE_CODECS


# --------------------------------------------------------------------------
# Payload shapes
# --------------------------------------------------------------------------


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return float(value)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def part_payload(
    entry: FileEntry,
    duration: Any = None,
    started_at: Any = None,
    ended_at: Any = None,
) -> dict[str, Any]:
    """One file. ``bytes`` always comes from disk, never from the manifest.

    A manifest written by a backend that was then killed records what it knew
    at the time; the file kept growing afterwards.
    """
    return {
        "file": entry.name,
        "bytes": entry.size,
        "duration": _number_or_none(duration),
        "started_at": _number_or_none(started_at),
        "ended_at": _number_or_none(ended_at),
        "modified_at": entry.mtime_ns / 1_000_000_000,
        "codec": None,
        "width": None,
        "height": None,
        "playable": None,
    }


def source_payload(
    source_id: str,
    kind: Any,
    codec: Any,
    status: Any,
    restarts: Any,
    error: Any,
    parts: list[dict[str, Any]],
) -> dict[str, Any]:
    """One camera's share of a session.

    ``recorded`` is the question the page exists to answer: a source listed
    here with no parts was configured, was recorded, and produced nothing.
    """
    durations = [part["duration"] for part in parts if part["duration"] is not None]
    return {
        "id": source_id,
        "kind": _text_or_none(kind),
        "codec": _text_or_none(codec),
        "status": _text_or_none(status) or "unknown",
        "restarts": _int_or_none(restarts),
        "error": _text_or_none(error),
        "recorded": bool(parts),
        "bytes": sum(part["bytes"] for part in parts),
        "part_count": len(parts),
        # Only when every part is accounted for; a partial sum would read as a
        # short recording rather than an incomplete measurement.
        "duration": (
            round(sum(durations), 3) if parts and len(durations) == len(parts) else None
        ),
        "parts": parts,
    }


def session_payload(
    session_id: str,
    started_at: float | None,
    ended_at: float | None,
    status: str,
    stopped_reason: Any,
    has_manifest: bool,
    active: bool,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    # Only from the recorder's own record of when it stopped. Without a
    # manifest, ``ended_at`` is the newest file's mtime, which says when the
    # folder was last touched -- a file copied or restored years later would
    # otherwise read as a recording that ran for years.
    duration = None
    if has_manifest and started_at is not None and ended_at is not None:
        if ended_at >= started_at:
            duration = round(ended_at - started_at, 3)
    return {
        "id": session_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration": duration,
        "status": status,
        "stopped_reason": _text_or_none(stopped_reason),
        "manifest": has_manifest,
        "active": active,
        "bytes": sum(source["bytes"] for source in sources),
        "part_count": sum(source["part_count"] for source in sources),
        "source_count": len(sources),
        "sources": sources,
    }


def without_parts(session: dict[str, Any]) -> dict[str, Any]:
    """The same session with per-file detail dropped, for the list endpoint."""
    trimmed = dict(session)
    trimmed["sources"] = [
        {key: value for key, value in source.items() if key != "parts"}
        for source in session["sources"]
    ]
    return trimmed


def library_payload(
    root: str,
    available: bool,
    free_bytes: int | None,
    total_bytes: int | None,
    active_session_id: str | None,
    sessions: list[dict[str, Any]],
) -> dict[str, Any]:
    """The whole library. An object, not a bare array, like storage_targets()."""
    return {
        "root": root,
        "available": available,
        "free_bytes": free_bytes,
        "total_bytes": total_bytes,
        "active_session_id": active_session_id,
        "session_count": len(sessions),
        "sessions": sessions,
    }


# --------------------------------------------------------------------------
# Reconciling a manifest against what is on disk
# --------------------------------------------------------------------------


def valid_manifest(manifest: Any) -> dict[str, Any] | None:
    """Whether a parsed ``session.json`` is one this build understands."""
    if not isinstance(manifest, dict):
        return None
    if manifest.get("version") != MANIFEST_VERSION:
        return None
    if not isinstance(manifest.get("sources"), list):
        return None
    return manifest


def _sources_from_files(files: Iterable[FileEntry]) -> list[dict[str, Any]]:
    """Group loose files by the source id in their names."""
    grouped: dict[str, list[FileEntry]] = {}
    for entry in files:
        parsed = parse_part_name(entry.name)
        if parsed is None:
            continue
        grouped.setdefault(parsed[0], []).append(entry)
    return [
        source_payload(
            source_id,
            None,
            None,
            "unknown",
            None,
            None,
            [part_payload(entry) for entry in sorted(entries)],
        )
        for source_id, entries in sorted(grouped.items())
    ]


def session_from_files(
    session_id: str, files: list[FileEntry], active: bool
) -> dict[str, Any]:
    """A session with no usable manifest: everything the names can tell us."""
    sources = _sources_from_files(files)
    ended_at = max((entry.mtime_ns for entry in files), default=None)
    return session_payload(
        session_id,
        session_started_at(session_id),
        None if ended_at is None else ended_at / 1_000_000_000,
        "recording" if active else "scanned",
        None,
        False,
        active,
        sources,
    )


def session_from_manifest(
    session_id: str, manifest: dict[str, Any], files: list[FileEntry], active: bool
) -> dict[str, Any]:
    """A session the recorder described, corrected against the filesystem."""
    by_name = {entry.name: entry for entry in files}
    claimed: set[str] = set()
    sources: list[dict[str, Any]] = []

    for raw in manifest["sources"]:
        if not isinstance(raw, dict):
            continue
        source_id = _text_or_none(raw.get("id"))
        if source_id is None:
            continue
        parts: list[dict[str, Any]] = []
        for raw_part in raw.get("parts") or []:
            if not isinstance(raw_part, dict):
                continue
            entry = by_name.get(raw_part.get("file"))
            # Gone means the operator deleted it by hand. The source stays.
            if entry is None:
                continue
            claimed.add(entry.name)
            parts.append(
                part_payload(
                    entry,
                    raw_part.get("duration"),
                    raw_part.get("started_at"),
                    raw_part.get("ended_at"),
                )
            )
        # A file on disk the manifest never got to mention: the part that was
        # being written when the backend was killed. Its duration is unknown
        # here and is filled in by a probe when the session is opened.
        for entry in files:
            parsed = parse_part_name(entry.name)
            if parsed is None or parsed[0] != source_id or entry.name in claimed:
                continue
            claimed.add(entry.name)
            parts.append(part_payload(entry))
        parts.sort(key=lambda part: part["file"])
        sources.append(
            source_payload(
                source_id,
                raw.get("kind"),
                raw.get("codec"),
                _source_status(raw.get("status"), manifest.get("status"), active),
                raw.get("restarts"),
                raw.get("error"),
                parts,
            )
        )

    leftovers = [entry for entry in files if entry.name not in claimed]
    sources.extend(_sources_from_files(leftovers))

    return session_payload(
        session_id,
        _number_or_none(manifest.get("started_at")) or session_started_at(session_id),
        _number_or_none(manifest.get("ended_at")),
        _session_status(manifest.get("status"), active),
        manifest.get("stopped_reason"),
        True,
        active,
        sources,
    )


def _session_status(manifest_status: Any, active: bool) -> str:
    """What the session is now, which the manifest alone cannot say.

    A manifest still reading ``recording`` for a session the recorder is not
    running was written by a backend that never got to stop. Detecting that
    costs nothing and needs no periodic rewrite of the file.
    """
    if active:
        return "recording"
    return "complete" if manifest_status == "complete" else "interrupted"


def _source_status(source_status: Any, manifest_status: Any, active: bool) -> Any:
    if active or manifest_status == "complete":
        return source_status
    if source_status in ("starting", "recording", "reconnecting"):
        return "interrupted"
    return source_status


# --------------------------------------------------------------------------
# Blocking filesystem work -- always called through asyncio.to_thread
# --------------------------------------------------------------------------


def list_files(session_dir: Path) -> list[FileEntry]:
    """Every recorded file in one session directory, by name."""
    entries: list[FileEntry] = []
    try:
        with os.scandir(session_dir) as listing:
            for item in listing:
                if parse_part_name(item.name) is None:
                    continue
                try:
                    if not item.is_file():
                        continue
                    stat_result = item.stat()
                except OSError:
                    continue
                entries.append(
                    FileEntry(item.name, stat_result.st_size, stat_result.st_mtime_ns)
                )
    except OSError:
        return []
    entries.sort()
    return entries


def read_manifest(session_dir: Path) -> dict[str, Any] | None:
    """Parse ``session.json``, or report that there is no usable one.

    A missing, torn, or unrecognised-version manifest is not an error: the
    caller falls back to the filenames, which is the same path a recording made
    before manifests existed takes.
    """
    try:
        text = (session_dir / MANIFEST_NAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        return valid_manifest(json.loads(text))
    except ValueError:
        return None


def load_session(session_dir: Path, session_id: str, active: bool) -> dict[str, Any]:
    files = list_files(session_dir)
    manifest = read_manifest(session_dir)
    if manifest is None:
        return session_from_files(session_id, files, active)
    return session_from_manifest(session_id, manifest, files, active)


def session_names(root: Path) -> list[str]:
    """Session directories in the root, newest first."""
    names: list[str] = []
    try:
        with os.scandir(root) as listing:
            for item in listing:
                if not SESSION_NAME_RE.match(item.name):
                    continue
                try:
                    # follow_symlinks=False so a link pointing out of the root
                    # is not listed; session_path refuses to resolve one, so
                    # listing it would offer a session nothing else can open.
                    if item.is_dir(follow_symlinks=False):
                        names.append(item.name)
                except OSError:
                    continue
    except OSError:
        return []
    names.sort(reverse=True)
    return names


def scan_library(root: Path | str, active_session_id: str | None) -> dict[str, Any]:
    """Every session under the recordings root.

    An unavailable root -- the card is out -- is a valid answer, not an error:
    the same stance ``storage_targets`` takes. The caller renders an empty
    library rather than a failure.
    """
    root = Path(root)
    available = root.is_dir()
    free_bytes: int | None = None
    total_bytes: int | None = None
    sessions: list[dict[str, Any]] = []
    if available:
        try:
            usage = shutil.disk_usage(root)
            free_bytes, total_bytes = usage.free, usage.total
        except OSError:
            pass
        sessions = [
            without_parts(
                load_session(root / name, name, name == active_session_id)
            )
            for name in session_names(root)
        ]
    return library_payload(
        str(root), available, free_bytes, total_bytes, active_session_id, sessions
    )


def directory_bytes(path: Path) -> int:
    total = 0
    for current, _directories, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(current, name)).st_size
            except OSError:
                continue
    return total


def remove_session(path: Path) -> tuple[int, int | None]:
    """Delete one session directory; report what it held and what is free now."""
    freed = directory_bytes(path)
    shutil.rmtree(path)
    try:
        return freed, shutil.disk_usage(path.parent).free
    except OSError:
        return freed, None


# --------------------------------------------------------------------------
# ffprobe
# --------------------------------------------------------------------------


def probe_args(path: str) -> tuple[str, ...]:
    return (
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "--",
        path,
    )


def _probe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        return None
    return number


def parse_probe_output(text: str) -> dict[str, Any]:
    """Duration, video codec and size, and the audio codec, from one ffprobe.

    ``N/A`` and missing fields read as unknown rather than raising, the same
    tolerance ``progress_media_microseconds`` has for ffmpeg's own output.
    """
    empty: dict[str, Any] = {
        "duration": None,
        "codec": None,
        "width": None,
        "height": None,
        "audio_codec": None,
    }
    try:
        data = json.loads(text)
    except ValueError:
        return empty
    if not isinstance(data, dict):
        return empty

    result = dict(empty)
    container = data.get("format")
    if isinstance(container, dict):
        result["duration"] = _probe_float(container.get("duration"))

    streams = data.get("streams")
    for stream in streams if isinstance(streams, list) else []:
        if not isinstance(stream, dict):
            continue
        kind = stream.get("codec_type")
        if kind == "video" and result["codec"] is None:
            result["codec"] = _text_or_none(stream.get("codec_name"))
            result["width"] = _int_or_none(stream.get("width"))
            result["height"] = _int_or_none(stream.get("height"))
            if result["duration"] is None:
                result["duration"] = _probe_float(stream.get("duration"))
        elif kind == "audio" and result["audio_codec"] is None:
            result["audio_codec"] = _text_or_none(stream.get("codec_name"))
    return result


# Keyed by path plus size plus mtime, so the part being written right now is
# re-probed instead of serving a stale, short duration, and so a path that was
# deleted and recreated cannot hit. Not persisted: a cold cache costs one
# ffprobe per part, once per backend run, and a cache file would have to live
# either on the operator's card or nowhere.
_PROBE_CACHE: "OrderedDict[tuple[str, int, int], dict[str, Any]]" = OrderedDict()
_PROBE_LIMIT = asyncio.Semaphore(PROBE_CONCURRENCY)


def _cache_probe(key: tuple[str, int, int], value: dict[str, Any]) -> None:
    _PROBE_CACHE[key] = value
    _PROBE_CACHE.move_to_end(key)
    while len(_PROBE_CACHE) > PROBE_CACHE_MAX:
        _PROBE_CACHE.popitem(last=False)


async def probe_part(path: Path, logger: logging.Logger) -> dict[str, Any]:
    """Measure one file. Never raises; unknown is a valid answer."""
    unknown = {
        "duration": None,
        "codec": None,
        "width": None,
        "height": None,
        "audio_codec": None,
    }
    try:
        stat_result = await asyncio.to_thread(os.stat, path)
    except OSError:
        return unknown
    key = (str(path), stat_result.st_size, stat_result.st_mtime_ns)
    cached = _PROBE_CACHE.get(key)
    if cached is not None:
        _PROBE_CACHE.move_to_end(key)
        return cached

    async with _PROBE_LIMIT:
        cached = _PROBE_CACHE.get(key)
        if cached is not None:
            return cached
        try:
            process = await asyncio.create_subprocess_exec(
                *probe_args(str(path)),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, ValueError) as error:
            logger.warning("Could not probe %s: %s", path.name, error)
            return unknown
        try:
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(), PROBE_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            with suppress(OSError):
                process.kill()
            await process.wait()
            logger.warning("Probing %s timed out", path.name)
            return unknown
        result = parse_probe_output(stdout.decode("utf-8", "replace"))
        _cache_probe(key, result)
        return result


async def fill_part_details(
    session: dict[str, Any], session_dir: Path, logger: logging.Logger
) -> dict[str, Any]:
    """Probe the parts of one opened session, in place.

    Only the detail endpoint does this. Probing the whole library on every list
    would be one forked ffprobe per file on a card that can hold hundreds.
    """
    probes = []
    for source in session["sources"]:
        for part in source["parts"]:
            probes.append((part, probe_part(session_dir / part["file"], logger)))
    results = await asyncio.gather(*(probe for _part, probe in probes))
    for (part, _probe), measured in zip(probes, results):
        if part["duration"] is None:
            part["duration"] = measured["duration"]
        part["codec"] = measured["codec"]
        part["width"] = measured["width"]
        part["height"] = measured["height"]
        part["playable"] = is_browser_playable(measured["codec"])

    # The aggregates were computed before the durations were known.
    for source in session["sources"]:
        parts = source["parts"]
        durations = [part["duration"] for part in parts if part["duration"] is not None]
        source["duration"] = (
            round(sum(durations), 3) if parts and len(durations) == len(parts) else None
        )
    return session


# --------------------------------------------------------------------------
# Playback
# --------------------------------------------------------------------------


def remux_args(
    path: str,
    start: float = 0.0,
    transcode: bool = False,
    audio_codec: str | None = None,
) -> tuple[str, ...]:
    """Stream one part out as fragmented MP4.

    Fragmented because a plain MP4 has to seek backwards to write its ``moov``
    atom and so cannot be produced on a pipe at all. The consequence is that
    the response has no index and honours no Range: playback is forward-only,
    and seeking is a new request with ``?t=``.

    ``-ss`` goes before ``-i`` so ffmpeg seeks by index instead of decoding the
    file from the beginning. ``-map 0:v:0 -map 0:a?`` mirrors the recorder's
    own reasoning: a private data track the camera sends would fail the mux.
    """
    seek: tuple[str, ...] = ("-ss", f"{start:.3f}") if start > 0 else ()
    if transcode:
        video: tuple[str, ...] = (
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
        )
    else:
        video = ("-c:v", "copy")
    audio_copyable = (audio_codec or "").lower() in MP4_AUDIO_COPY_CODECS
    audio: tuple[str, ...] = ("-c:a", "copy") if audio_copyable else ("-c:a", "aac")
    return (
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-loglevel",
        "error",
        *seek,
        "-i",
        path,
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-dn",
        "-sn",
        *video,
        *audio,
        "-movflags",
        "+frag_keyframe+empty_moov+default_base_moof",
        # A dead man for a corrupt input. It can never truncate a real
        # recording: every part is already capped at the same limit.
        "-t",
        str(MAX_SESSION_S),
        "-f",
        "mp4",
        "pipe:1",
    )


# Checked before the response is constructed rather than awaited inside the
# generator: a request that waits with its headers already sent shows up in the
# browser as a video that never starts, which is worse than a clear refusal.
_active_remuxes = 0


def acquire_remux_slot() -> bool:
    global _active_remuxes
    if _active_remuxes >= REMUX_MAX_CONCURRENT:
        return False
    _active_remuxes += 1
    return True


def release_remux_slot() -> None:
    global _active_remuxes
    _active_remuxes = max(_active_remuxes - 1, 0)


async def _drain_stderr(process: asyncio.subprocess.Process, tail: list[str]) -> None:
    """Keep the pipe empty. A blocked ffmpeg stops producing video entirely."""
    if process.stderr is None:
        return
    try:
        async for line in process.stderr:
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                tail.append(scrub_credentials(text))
                del tail[:-REMUX_STDERR_TAIL]
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - stderr is best-effort
        return


async def stream_remux(
    path: Path,
    logger: logging.Logger,
    start: float = 0.0,
    transcode: bool = False,
    audio_codec: str | None = None,
) -> AsyncIterator[bytes]:
    """Yield the remuxed bytes, and take the ffmpeg down with the response."""
    args = remux_args(str(path), start, transcode, audio_codec)
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=die_with_parent,
    )
    tail: list[str] = []
    reader = asyncio.create_task(_drain_stderr(process, tail))
    try:
        assert process.stdout is not None
        while True:
            chunk = await process.stdout.read(REMUX_CHUNK_BYTES)
            if not chunk:
                break
            yield chunk
        returncode = await process.wait()
        if returncode not in (0, None):
            logger.warning(
                "Playback of %s exited %s: %s",
                path.name,
                returncode,
                tail[-1] if tail else "no error output",
            )
    finally:
        # Reached when the viewer navigates away mid-clip, which closes the
        # generator. Kill rather than SIGINT: there is no trailer worth waiting
        # for and nobody left to read it. The wait() is what reaps the child.
        if process.returncode is None:
            with suppress(OSError):
                process.kill()
            await process.wait()
        reader.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await reader


# --------------------------------------------------------------------------
# Thumbnails
# --------------------------------------------------------------------------


def thumbnail_args(
    path: str, offset: float, width: int = THUMBNAIL_WIDTH
) -> tuple[str, ...]:
    """One JPEG frame on stdout. Nothing is written to the recordings folder."""
    seek: tuple[str, ...] = ("-ss", f"{offset:.3f}") if offset > 0 else ()
    return (
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-loglevel",
        "error",
        *seek,
        "-i",
        path,
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:-2",
        "-q:v",
        THUMBNAIL_QUALITY,
        "-f",
        "mjpeg",
        "pipe:1",
    )


def thumbnail_offset(duration: float | None) -> float:
    """A second in, unless the clip is shorter than that."""
    if duration is None or duration <= 0:
        return 1.0
    return min(1.0, duration / 2)


def thumbnail_cache_key(path: Path, size: int, mtime_ns: int, width: int) -> str:
    material = f"{path.resolve()}|{size}|{mtime_ns}|{width}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def thumbnail_cache_dir() -> Path:
    """Where posters are kept. Never the recordings folder.

    That folder is usually a card the operator browses and which the recorder
    itself refuses to write to when it is nearly full -- turning a read into a
    write there is the wrong trade for a file that costs one seek to rebuild.
    """
    configured = os.environ.get(THUMBNAIL_CACHE_ENV)
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "steamdeck-robot-monitor-thumbs"


def _trim_thumbnail_cache(directory: Path) -> None:
    try:
        files = sorted(
            (item for item in directory.iterdir() if item.is_file()),
            key=lambda item: item.stat().st_mtime_ns,
        )
    except OSError:
        return
    for item in files[: max(len(files) - THUMBNAIL_CACHE_MAX, 0)]:
        with suppress(OSError):
            item.unlink()


def _read_cached_thumbnail(cache: Path) -> bytes | None:
    try:
        return cache.read_bytes()
    except OSError:
        return None


def _store_thumbnail(cache: Path, data: bytes) -> None:
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix(".part")
        temporary.write_bytes(data)
        os.replace(temporary, cache)
    except OSError:
        return
    _trim_thumbnail_cache(cache.parent)


async def _run_thumbnail(args: tuple[str, ...]) -> bytes | None:
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=die_with_parent,
        )
    except (OSError, ValueError):
        return None
    try:
        stdout, _stderr = await asyncio.wait_for(
            process.communicate(), THUMBNAIL_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        with suppress(OSError):
            process.kill()
        await process.wait()
        return None
    return stdout or None


async def render_thumbnail(
    path: Path, width: int, logger: logging.Logger
) -> tuple[bytes | None, str | None]:
    """A poster frame and its cache key, rendering it only if we have to."""
    try:
        stat_result = await asyncio.to_thread(os.stat, path)
    except OSError:
        return None, None
    key = thumbnail_cache_key(path, stat_result.st_size, stat_result.st_mtime_ns, width)
    cache = thumbnail_cache_dir() / f"{key}.jpg"
    cached = await asyncio.to_thread(_read_cached_thumbnail, cache)
    if cached:
        return cached, key

    measured = await probe_part(path, logger)
    offset = thumbnail_offset(measured["duration"])
    data = await _run_thumbnail(thumbnail_args(str(path), offset, width))
    if not data and offset > 0:
        # The clip is shorter than the offset, so the seek landed past its end
        # and produced nothing at all.
        data = await _run_thumbnail(thumbnail_args(str(path), 0.0, width))
    if not data:
        return None, key
    await asyncio.to_thread(_store_thumbnail, cache, data)
    return data, key
