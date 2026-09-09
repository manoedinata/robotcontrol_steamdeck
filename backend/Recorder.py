"""Record every configured camera source to disk at once.

One ffmpeg child per source, stream-copied into Matroska. Nothing is
re-encoded: the Deck is already decoding one live stream per source for the
UI, and it must not also pay for an encode.

Matroska rather than MP4 because a robot loses power mid-mission. ffmpeg writes
MKV clusters incrementally under an unknown-length segment, so a file whose
writer was killed still plays and ``ffmpeg -i in.mkv -c copy out.mkv`` rebuilds
its index. An MP4 truncated the same way never wrote its ``moov`` atom and is a
total loss.

Because every camera source now reaches the backend the same way (see
``CameraWebSocketSource``), there is one recording path: RTSP sources are dialed
at their origin, and WebSocket sources are read from the local relay, which
costs no extra camera connection because the hub is already holding one.
"""

import asyncio
import ctypes
import logging
import os
import re
import shutil
import signal
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from CameraWebSocketSource import is_websocket_url, relay_url

# Where sessions are written. A deployment concern like APP_SETTINGS_DIR, not a
# config key: a filesystem path arriving from the renderer over a WebSocket
# would be a path-injection surface for no benefit.
RECORDINGS_DIR_ENV = "RECORDINGS_DIR"

# Refuse to start below the first, stop cleanly below the second. Stopping on
# purpose trailers every file; running into ENOSPC leaves N broken ones.
MIN_FREE_START_BYTES = 2 * 1024**3
MIN_FREE_STOP_BYTES = 512 * 1024**2

# A source whose file has not grown for this long is wedged. ffmpeg will sit
# forever on an RTSP session that stopped delivering frames without erroring or
# exiting, and no socket timeout catches it because the socket is alive.
STALL_TIMEOUT_S = 15.0

# Restart pacing after an ffmpeg exits while recording is still wanted.
RESTART_BASE_S = 1.0
RESTART_MAX_S = 15.0
# An ffmpeg that ran at least this long was working; its failure is an event,
# not a misconfiguration, so the restart counter resets.
HEALTHY_RUNTIME_S = 15.0
# One that dies faster than this, this many times running, is misconfigured.
FAST_FAILURE_S = 3.0
MAX_FAST_FAILURES = 3

# Dead-man limit per file, so an ffmpeg orphaned by a SIGKILLed backend
# eventually releases the camera instead of filling the disk forever.
MAX_SESSION_S = 4 * 60 * 60

# How long a stopping ffmpeg gets to write its Matroska trailer.
FINALIZE_TIMEOUT_S = 5.0

# ffmpeg stderr kept per source, to surface the reason a source failed.
STDERR_TAIL_LINES = 20

# prctl(2) option number for the parent-death signal. Linux only.
PR_SET_PDEATHSIG = 1

# Loaded once at import, never inside the pre-exec hook: that hook runs between
# fork and exec, where doing as little as possible is what keeps it safe.
try:
    _LIBC: ctypes.CDLL | None = ctypes.CDLL("libc.so.6", use_errno=True)
except OSError:  # pragma: no cover - non-glibc platforms
    _LIBC = None

# Captured before any child exists, so the hook can tell "my parent died" from
# "my parent legitimately is PID 1", which is the case when uvicorn runs as the
# container's init process.
_BACKEND_PID = os.getpid()


def die_with_parent() -> None:
    """Ask the kernel to signal this ffmpeg when the backend process dies.

    Without this, a backend killed outright (SIGKILL, an OOM kill, a crash)
    leaves its recorders running: they hold a camera session open and keep
    filling the disk until their own time limit expires.

    SIGTERM rather than SIGKILL, so ffmpeg still flushes and writes its
    Matroska trailer on the way out and the recording stays a valid file.

    Runs in the child between fork and exec.
    """
    if _LIBC is not None:
        _LIBC.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    # PDEATHSIG only fires on a *future* death, so a parent that died between
    # the fork and this call would never be noticed. Checking here closes that
    # race; comparing against the recorded pid rather than 1 keeps it correct
    # when the backend itself is PID 1.
    if os.getppid() != _BACKEND_PID:
        os._exit(1)


_USERINFO_RE = re.compile(r"://[^/@\s]*@")
_UNSAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9_.-]")


def session_dir_name(now: datetime) -> str:
    """Name the directory holding one press of the record button.

    No colons: the Deck's SD card is usually exFAT, where they are illegal.
    This layout also sorts chronologically.
    """
    return now.strftime("%Y%m%d-%H%M%S")


def safe_component(name: str) -> str:
    """Reduce one path segment to an unambiguous alphabet."""
    cleaned = _UNSAFE_COMPONENT_RE.sub("_", name)
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError(f"unsafe path component: {name!r}")
    return cleaned


def recording_path(root: Path, session: str, source_id: str, part: int) -> Path:
    """Where one part of one source's recording is written.

    Stream ids are already constrained by the config validator, so sanitizing
    here is defence in depth -- but the result is handed to a subprocess, so it
    is also checked to still be inside the recordings root.
    """
    root = Path(root)
    path = root / safe_component(session) / f"{safe_component(source_id)}_{part:03d}.mkv"
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("recording path escapes the recordings directory")
    return resolved


def parse_ffmpeg_major(version_output: str) -> int:
    """Read the major version out of an ``ffmpeg -version`` banner."""
    match = re.search(r"ffmpeg version n?(\d+)", version_output)
    # Assume the oldest supported option spelling when the banner is
    # unreadable: a wrong guess fails instantly and visibly, and the Docker
    # image ships the older ffmpeg anyway.
    return int(match.group(1)) if match else 5


def rtsp_timeout_option(ffmpeg_major: int) -> tuple[str, ...]:
    """The RTSP socket-timeout option, whose name changed in ffmpeg 6.

    Microseconds. The Docker image ships ffmpeg 5.x while a development host
    may have 6 or newer, so this cannot be hardcoded.
    """
    name = "-timeout" if ffmpeg_major >= 6 else "-stimeout"
    return (name, "5000000")


def rtsp_record_args(
    url: str,
    output: str,
    timeout_option: tuple[str, ...],
    max_seconds: int = MAX_SESSION_S,
) -> tuple[str, ...]:
    """ffmpeg arguments for recording one RTSP source.

    TCP, deliberately unlike the live path's UDP: display wants latency, but a
    dropped RTP packet is permanent corruption of that GOP in a stream copy and
    there is no second chance at it. Recording has no latency requirement.

    No wallclock timestamps here -- RTSP carries RTP timing, and overriding it
    would make the recording worse.
    """
    return (
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-loglevel",
        "error",
        # Input options; meaningless after -i.
        "-rtsp_transport",
        "tcp",
        *timeout_option,
        "-i",
        url,
        # Take video, and audio only when the camera has it. "-map 0" instead
        # picks up Hikvision's private data stream and fails the mux with
        # "Could not find tag for codec none".
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-dn",
        "-sn",
        "-c",
        "copy",
        # Write through instead of sitting in ffmpeg's AVIO buffer. The stall
        # watchdog and the HUD byte counter both read the file's size, and an
        # unflushed recording looks identical to a wedged one. It also leaves
        # less unwritten data to lose when the power goes.
        "-flush_packets",
        "1",
        "-t",
        str(max_seconds),
        # Explicit: never infer the container from an operator-influenced path.
        "-f",
        "matroska",
        "-y",
        output,
    )


def relay_record_args(
    input_format: str,
    url: str,
    output: str,
    max_seconds: int = MAX_SESSION_S,
) -> tuple[str, ...]:
    """ffmpeg arguments for recording one relayed camera WebSocket source.

    The relay serves a bare bytestream, so the demuxer has to be named and the
    packets need wallclock stamps: without them ffmpeg assumes 25 fps, so a
    30 fps camera yields a file 20% longer than real time and a network stall
    compresses to nothing. Stamped, duration tracks the wall clock and a stall
    shows up as one long frame.
    """
    return (
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-loglevel",
        "error",
        "-use_wallclock_as_timestamps",
        "1",
        "-fflags",
        "+genpts",
        "-f",
        input_format,
        "-i",
        url,
        "-an",
        "-dn",
        "-sn",
        "-c",
        "copy",
        # Write through instead of sitting in ffmpeg's AVIO buffer. The stall
        # watchdog and the HUD byte counter both read the file's size, and an
        # unflushed recording looks identical to a wedged one. It also leaves
        # less unwritten data to lose when the power goes.
        "-flush_packets",
        "1",
        "-t",
        str(max_seconds),
        "-f",
        "matroska",
        "-y",
        output,
    )


def backoff_delay(attempt: int) -> float:
    """How long to wait before restarting a source's recording."""
    return min(RESTART_BASE_S * (2 ** max(attempt - 1, 0)), RESTART_MAX_S)


def classify_exit(
    returncode: int | None,
    ran_seconds: float,
    attempt: int,
    ever_recorded: bool = False,
) -> str:
    """Whether a dead ffmpeg is worth restarting.

    A source that has already written video once is known-good, so every later
    failure is an outage and is retried for as long as the session lasts. A
    camera that reboots, or a robot that drives out of range, fails *fast* --
    the dial is refused immediately -- so giving up on repeated quick failures
    would abandon exactly the sources most worth waiting for.

    Only a source that has never produced a single byte is treated as
    misconfigured (bad credentials, wrong path, unreachable host, bad option).
    Retrying that forever would just hammer the camera, so it stops.
    """
    if ever_recorded:
        return "retry"
    if ran_seconds >= FAST_FAILURE_S:
        return "retry"
    return "give_up" if attempt >= MAX_FAST_FAILURES else "retry"


def scrub_credentials(text: str) -> str:
    """Strip userinfo from any URL in a message.

    ffmpeg reports failures with the input URL intact
    (``rtsp://admin:pw@host/s: 401 Unauthorized``), and this text reaches the
    log and every connected UI.
    """
    return _USERINFO_RE.sub("://***@", text)


def is_stalled(bytes_now: int, bytes_then: int, seconds_since: float) -> bool:
    """Whether a recording has stopped growing for long enough to be wedged."""
    return bytes_now <= bytes_then and seconds_since >= STALL_TIMEOUT_S


def has_free_space(free_bytes: int, minimum_bytes: int) -> bool:
    return free_bytes >= minimum_bytes


def seconds_remaining(free_bytes: int, bytes_per_second: float | None) -> float | None:
    """Recording time left at the observed write rate, or None until measured."""
    if not bytes_per_second or bytes_per_second <= 0:
        return None
    return max(free_bytes - MIN_FREE_STOP_BYTES, 0) / bytes_per_second


@dataclass
class SourceRecording:
    """Per-source recording state. ``url`` is never broadcast or logged."""

    source_id: str
    kind: str
    url: str
    input_format: str | None = None
    status: str = "idle"
    part: int = 0
    filename: str = ""
    bytes_written: int = 0
    restarts: int = 0
    error: str | None = None
    stderr_tail: deque = field(default_factory=lambda: deque(maxlen=STDERR_TAIL_LINES))


def source_state_payload(state: SourceRecording) -> dict[str, Any]:
    """The public view of one source. Deliberately excludes the camera URL."""
    return {
        "id": state.source_id,
        "kind": state.kind,
        "status": state.status,
        "part": state.part,
        "file": state.filename,
        "bytes": state.bytes_written,
        "restarts": state.restarts,
        "error": state.error,
    }


def recording_state_payload(
    active: bool,
    session: str | None,
    started_at: float | None,
    directory: str | None,
    free_bytes: int | None,
    remaining: float | None,
    stopped_reason: str | None,
    sources: list[SourceRecording],
) -> dict[str, Any]:
    return {
        "type": "recording",
        "active": active,
        "session_id": session,
        "started_at": started_at,
        "directory": directory,
        "free_bytes": free_bytes,
        "seconds_remaining": remaining,
        "stopped_reason": stopped_reason,
        "sources": [source_state_payload(state) for state in sources],
    }


def normalize_record_action(value: Any) -> str:
    """Validate the UI's record request."""
    if not isinstance(value, str):
        raise ValueError("record action must be a string")
    action = value.strip().lower()
    if action not in {"start", "stop"}:
        raise ValueError("record action must be 'start' or 'stop'")
    return action


class _Writer:
    """Supervise one source's ffmpeg for the life of a recording session."""

    def __init__(
        self,
        state: SourceRecording,
        session_dir: Path,
        root: Path,
        session: str,
        timeout_option: tuple[str, ...],
        logger: logging.Logger,
        on_change: Callable[[], None],
    ) -> None:
        self._state = state
        self._session_dir = session_dir
        self._root = root
        self._session = session
        self._timeout_option = timeout_option
        self._logger = logger
        self._on_change = on_change
        self._process: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._path: Path | None = None
        # Set once this source has actually written video. It separates a
        # camera that is merely away from one that was never reachable.
        self._ever_recorded = False
        # Stall tracking, driven by the state loop rather than a timer here.
        self._last_size = 0
        self._last_growth = 0.0

    @property
    def state(self) -> SourceRecording:
        return self._state

    def start(self) -> None:
        self._task = asyncio.create_task(
            self._supervise(), name=f"record-{self._state.source_id}"
        )

    def _set_status(self, status: str, error: str | None = None) -> None:
        self._state.status = status
        self._state.error = error
        self._on_change()

    def _args(self, output: str) -> tuple[str, ...]:
        if self._state.kind == "websocket":
            return relay_record_args(
                self._state.input_format or "h264",
                relay_url(self._state.source_id),
                output,
            )
        return rtsp_record_args(self._state.url, output, self._timeout_option)

    async def _supervise(self) -> None:
        attempt = 0
        loop = asyncio.get_running_loop()
        while not self._stopping:
            self._state.part += 1
            self._path = recording_path(
                self._root, self._session, self._state.source_id, self._state.part
            )
            self._state.filename = self._path.name
            self._set_status("starting")
            started = loop.time()
            try:
                await self._run_once(str(self._path))
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - reported, not raised
                self._state.error = scrub_credentials(str(error))
                self._logger.warning(
                    "Recording %s failed: %s", self._state.source_id, self._state.error
                )

            if self._stopping:
                break

            ran = loop.time() - started
            if self._wrote_video():
                self._ever_recorded = True
            if ran >= HEALTHY_RUNTIME_S:
                attempt = 0
            attempt += 1
            returncode = self._process.returncode if self._process else None
            if (
                classify_exit(returncode, ran, attempt, self._ever_recorded)
                == "give_up"
            ):
                self._set_status(
                    "failed", self._state.error or "recording stopped immediately"
                )
                return
            self._state.restarts += 1
            self._set_status("reconnecting", self._state.error)
            await asyncio.sleep(backoff_delay(attempt))

    def _wrote_video(self) -> bool:
        """Whether the part just finished actually holds video."""
        if self._path is None:
            return False
        try:
            return self._path.stat().st_size > 0
        except OSError:
            return False

    async def _run_once(self, output: str) -> None:
        args = self._args(output)
        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            # Children stay in the backend's process group, and the kernel
            # takes them down with it even if it is killed outright.
            preexec_fn=die_with_parent,
        )
        self._last_size = 0
        self._last_growth = asyncio.get_running_loop().time()
        self._set_status("recording", None)
        # stderr must be drained: with a PIPE and no reader, a chatty ffmpeg
        # fills the 64 KiB pipe buffer and blocks forever.
        reader = asyncio.create_task(self._read_stderr(self._process))
        try:
            await self._process.wait()
        finally:
            reader.cancel()
            if self._process.returncode not in (0, None) and self._state.stderr_tail:
                self._state.error = self._state.stderr_tail[-1]

    async def _read_stderr(self, process: asyncio.subprocess.Process) -> None:
        if process.stderr is None:
            return
        try:
            async for line in process.stderr:
                text = line.decode("utf-8", "replace").rstrip()
                if text:
                    # Scrubbed at the boundary so nothing downstream has to
                    # remember that these lines can carry credentials.
                    self._state.stderr_tail.append(scrub_credentials(text))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - stderr is best-effort
            return

    def poll_size(self, now: float) -> bool:
        """Refresh the byte counter; report whether the file looks wedged."""
        if self._state.status != "recording" or self._path is None:
            return False
        try:
            size = self._path.stat().st_size
        except OSError:
            return False
        self._state.bytes_written = size
        if size > self._last_size:
            self._last_size = size
            self._last_growth = now
            return False
        return is_stalled(size, self._last_size, now - self._last_growth)

    async def restart_stalled(self) -> None:
        """Kill a wedged ffmpeg so the supervisor rolls to a new part."""
        self._logger.warning(
            "Recording %s stopped growing; restarting it", self._state.source_id
        )
        self._last_growth = asyncio.get_running_loop().time()
        process = self._process
        if process is not None and process.returncode is None:
            process.kill()

    async def finalize(self) -> None:
        """Stop this source and let ffmpeg write its Matroska trailer."""
        self._stopping = True
        process = self._process
        if process is not None and process.returncode is None:
            # SIGINT is ffmpeg's documented graceful stop, the same path as
            # pressing q: stop reading, flush, write the trailer.
            with suppress(OSError):
                process.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(process.wait(), FINALIZE_TIMEOUT_S)
            except asyncio.TimeoutError:
                with suppress(OSError):
                    process.kill()

        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task

        self._drop_empty_output()
        if self._state.status != "failed":
            self._state.status = "stopped"

    def _drop_empty_output(self) -> None:
        """Remove a file ffmpeg never got far enough to write a header for."""
        if self._path is None:
            return
        try:
            if self._path.stat().st_size == 0:
                self._path.unlink()
                self._state.filename = ""
                self._state.error = self._state.error or "no video was received"
        except OSError:
            pass


class Recorder:
    """Own one recording session across every configured camera source."""

    def __init__(self, logger: logging.Logger, ws_hub: Any = None) -> None:
        # Assign fields and nothing else. server.py builds this at import time,
        # and the test suite imports server, so no environment reads, no path
        # resolution, no directory creation and no tasks may happen here.
        self._logger = logger
        self._ws_hub = ws_hub
        self._listener: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self._sources: tuple[tuple[str, str], ...] = ()
        # Operator override for the recordings location; empty falls back to
        # the deployment default. Like the source list, it is picked up by the
        # next session rather than applied to a running one.
        self._root_override: str = ""
        self._active_root: Path | None = None
        self._writers: list[_Writer] = []
        self._session: str | None = None
        self._started_at: float | None = None
        self._directory: str | None = None
        self._stopped_reason: str | None = None
        self._timeout_option: tuple[str, ...] | None = None
        self._free_bytes: int | None = None
        self._rate: float | None = None
        self._rate_sample: tuple[float, int] | None = None
        self._lock = asyncio.Lock()

    @property
    def active(self) -> bool:
        return self._session is not None

    def set_listener(
        self, listener: Callable[[dict[str, Any]], Awaitable[None]] | None
    ) -> None:
        """Attach the broadcast callback, which does not exist at import time."""
        self._listener = listener

    def set_root(self, path: str) -> None:
        """Choose where the next session writes. Empty restores the default."""
        if path == self._root_override:
            return
        self._root_override = path
        if self.active:
            self._logger.info(
                "Recording in progress; recordings directory change applies "
                "to the next session"
            )

    def update_sources(self, streams: tuple[tuple[str, str], ...]) -> None:
        """Adopt the configured sources for the *next* session.

        Never applied to a running one. Switching cameras in the UI re-sends
        the whole config, so reacting here would shred a recording into parts
        every time the operator pressed B.
        """
        self._sources = tuple(streams)
        if self.active:
            self._logger.info(
                "Recording in progress; source list change applies to the next session"
            )

    def state(self) -> dict[str, Any]:
        return recording_state_payload(
            active=self.active,
            session=self._session,
            started_at=self._started_at,
            directory=self._directory,
            free_bytes=self._free_bytes,
            remaining=(
                seconds_remaining(self._free_bytes, self._rate)
                if self._free_bytes is not None
                else None
            ),
            stopped_reason=self._stopped_reason,
            sources=[writer.state for writer in self._writers],
        )

    def _notify(self) -> None:
        if self._listener is None:
            return
        # Fire and forget: a slow client must never stall a supervisor.
        asyncio.create_task(self._listener(self.state()))

    def _root(self) -> Path:
        if self._root_override:
            return Path(self._root_override)
        default = Path(__file__).resolve().parent.parent / "recordings"
        return Path(os.environ.get(RECORDINGS_DIR_ENV, str(default)))

    async def _ffmpeg_timeout_option(self) -> tuple[str, ...]:
        if self._timeout_option is not None:
            return self._timeout_option
        banner = ""
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await process.communicate()
            banner = out.decode("utf-8", "replace")
        except OSError as error:
            raise ValueError(f"ffmpeg is not available: {error}") from error
        self._timeout_option = rtsp_timeout_option(parse_ffmpeg_major(banner))
        return self._timeout_option

    async def start(self) -> dict[str, Any]:
        async with self._lock:
            if self.active:
                return self.state()
            if not self._sources:
                raise ValueError("no camera source is configured")

            root = self._root()
            # Deliberately not created. The folder is usually removable media,
            # and creating it would mean inventing a directory wherever the
            # card is *not* -- on the container's own ephemeral filesystem, or
            # under /run, which is tmpfs, where a recording fills RAM until the
            # Deck runs out. An absent card must read as "not available", which
            # is exactly what a missing directory says.
            if not root.is_dir():
                raise ValueError(f"recordings folder is not available: {root}")
            if not os.access(root, os.W_OK):
                raise ValueError(f"recordings folder is not writable: {root}")

            usage = shutil.disk_usage(root)
            if not has_free_space(usage.free, MIN_FREE_START_BYTES):
                raise ValueError(
                    "not enough free space to record "
                    f"({usage.free // 1024**2} MiB available)"
                )

            timeout_option = await self._ffmpeg_timeout_option()
            session = session_dir_name(datetime.now())
            session_dir = root / session
            try:
                session_dir.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise ValueError(
                    f"recording directory is not available: {error}"
                ) from error

            self._writers = []
            for source_id, url in self._sources:
                kind = "websocket" if is_websocket_url(url) else "rtsp"
                input_format = None
                if kind == "websocket" and self._ws_hub is not None:
                    input_format = await self._ws_hub.detect_format(source_id, url)
                state = SourceRecording(
                    source_id=source_id,
                    kind=kind,
                    url=url,
                    input_format=input_format,
                )
                self._writers.append(
                    _Writer(
                        state,
                        session_dir,
                        root,
                        session,
                        timeout_option,
                        self._logger,
                        self._notify,
                    )
                )

            self._session = session
            self._active_root = root
            self._started_at = time.time()
            self._directory = str(session_dir)
            self._stopped_reason = None
            self._free_bytes = usage.free
            self._rate = None
            self._rate_sample = None
            for writer in self._writers:
                writer.start()

            self._logger.info(
                "Recording started: session=%s sources=%d directory=%s",
                session,
                len(self._writers),
                session_dir,
            )

        state = self.state()
        await self._emit(state)
        return state

    async def stop(self, reason: str = "operator") -> dict[str, Any]:
        async with self._lock:
            if not self.active:
                return self.state()
            writers = tuple(self._writers)
            # All sources in parallel, so stopping costs one timeout, not N.
            await asyncio.gather(
                *(writer.finalize() for writer in writers), return_exceptions=True
            )
            self._session = None
            self._started_at = None
            self._stopped_reason = reason
            self._logger.info("Recording stopped (%s)", reason)

        state = self.state()
        await self._emit(state)
        return state

    async def poll(self) -> dict[str, Any]:
        """Refresh counters, restart wedged sources, and stop on low disk."""
        if not self.active:
            return self.state()

        now = asyncio.get_running_loop().time()
        root = self._active_root or self._root()

        # The card was pulled. Every writer is about to fail on write, and each
        # has already recorded video, so the supervisors would retry them
        # forever against a directory that no longer exists.
        if not root.is_dir():
            self._logger.warning(
                "Recordings folder %s disappeared; stopping the recording", root
            )
            return await self.stop(reason="folder_lost")

        stalled = [writer for writer in self._writers if writer.poll_size(now)]
        for writer in stalled:
            await writer.restart_stalled()

        try:
            usage = shutil.disk_usage(root)
            self._free_bytes = usage.free
        except OSError:
            usage = None

        total = sum(writer.state.bytes_written for writer in self._writers)
        if self._rate_sample is not None:
            elapsed = now - self._rate_sample[0]
            if elapsed > 0:
                self._rate = max(total - self._rate_sample[1], 0) / elapsed
        self._rate_sample = (now, total)

        if usage is not None and not has_free_space(usage.free, MIN_FREE_STOP_BYTES):
            self._logger.warning("Free space exhausted; stopping the recording")
            return await self.stop(reason="low_disk")
        return self.state()

    async def _emit(self, state: dict[str, Any]) -> None:
        if self._listener is not None:
            await self._listener(state)

    async def close(self) -> None:
        if self.active:
            await self.stop(reason="shutdown")
