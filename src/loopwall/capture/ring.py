"""FFmpeg segment ring on tmpfs, rolling deletion; uvc and lavfi camera types.

Built at 0.1.0 build order step 6 (ROADMAP.md).
"""

from __future__ import annotations

import atexit
import csv
import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from loopwall.proctree import kill_tree

_WINDOWS = platform.system() == "Windows"


@dataclass(frozen=True)
class Segment:
    path: Path
    start: float
    end: float


class RingCapture:
    """Continuous FFmpeg segment-ring capture with guaranteed process teardown.

    A camera left recording by an un-stopped ffmpeg process is a real-world
    side effect, not just a resource leak - so teardown here is layered
    rather than relying on any single mechanism:

    1. Always use as a context manager (``with RingCapture(...) as cap:``);
       ``__exit__`` calls :meth:`stop` even on an exception.
    2. :meth:`start` registers :meth:`stop` with ``atexit`` as a fallback for
       normal interpreter shutdown without an explicit ``stop()``.
    3. ffmpeg itself is launched with a hard ``-t max_runtime_seconds`` cap,
       so the process self-terminates even if every Python-side teardown
       path is skipped (e.g. a hard process kill).

    ffmpeg is started via ``subprocess.Popen`` directly from this process,
    not via a shell background job. :meth:`stop` kills the whole process
    *tree*, not just the tracked PID - on at least one real setup, `ffmpeg`
    on PATH resolves to a package-manager launcher shim (e.g. Chocolatey's
    shimgen) that spawns the real binary as a child; terminating only the
    shim orphans that child with the camera still open. A single-PID kill is
    not sufficient regardless of platform.

    ``preview_tcp_port``, if set, duplicates the encoded stream (ffmpeg's
    ``tee`` muxer - one encode, two sinks) to ``tcp://127.0.0.1:<port>`` as
    MPEG-TS, alongside the ring - most camera devices/drivers refuse a
    second concurrent open (confirmed on this project's own dev hardware),
    so a genuinely live preview has to share the ring's single capture
    process rather than open the camera again. See PLAN.md §3.7.

    TCP, not UDP, and ffmpeg connects *out* as a client rather than
    listening - both confirmed necessary the hard way, not chosen upfront:
    UDP corrupted every frame with "non-existing PPS" decode errors
    (packet loss/reordering, even on loopback), and ffmpeg listening
    (``?listen=1``) blocked the *entire* tee output - ring included -
    until a viewer connected, which would make ring reliability depend on
    a preview window existing. The tee's preview slave also carries
    ``onfail=ignore``, so a viewer that never connects, or disconnects
    mid-session, drops only the preview - never the ring. Callers are
    expected to have a listener already bound on ``preview_tcp_port``
    (e.g. ``ffplay ... tcp://127.0.0.1:<port>?listen=1``) *before* calling
    :meth:`start` - see ``drivers/cli/wall.py``'s ordering. The current
    listener is ``server/relay.py``'s ``PreviewRelay`` (a plain asyncio
    TCP server, no ffmpeg) - this stream is raw MPEG-TS either way, so
    what's on the other end has changed twice without this class caring.
    """

    def __init__(
        self,
        ring_dir: Path,
        input_args: list[str],
        segment_seconds: float = 2.0,
        ring_seconds: float = 120.0,
        fps: int = 30,
        encoder: str = "libx264",
        preset: str = "veryfast",
        max_runtime_seconds: float = 600.0,
        preview_tcp_port: int | None = None,
    ) -> None:
        self.ring_dir = Path(ring_dir)
        self.input_args = input_args
        self.segment_seconds = segment_seconds
        self.ring_seconds = ring_seconds
        self.fps = fps
        self.encoder = encoder
        self.preset = preset
        self.max_runtime_seconds = max_runtime_seconds
        self.preview_tcp_port = preview_tcp_port
        self._proc: subprocess.Popen | None = None
        self._stderr_file: IO[bytes] | None = None
        self.t0: float | None = None

    @property
    def list_path(self) -> Path:
        return self.ring_dir / "list.csv"

    @property
    def stderr_path(self) -> Path:
        return self.ring_dir / "ffmpeg.stderr.log"

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def wait_until_ready(self, timeout: float = 2.0) -> bool:
        """Poll briefly to catch an immediate startup failure - a bad device, an
        unsupported resolution/framerate, or a device already held open
        elsewhere all make ffmpeg exit fast (confirmed empirically: well under
        a second, not a hang), so a short grace window is enough to tell
        "opened fine, still running" from "never actually started" without
        waiting for a full segment to close. Blocking (real `time.sleep`) -
        callers on an event loop should run this in an executor.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_running:
                return False
            time.sleep(0.1)
        return self.is_running

    def tail_stderr(self, max_bytes: int = 4000) -> str:
        """ffmpeg's own error output, for surfacing *why* a start failed -
        `start()` redirects stderr to `stderr_path` rather than the
        `DEVNULL` every other caller of this class gets, specifically so a
        failure has something to show a human instead of a bare nonzero
        exit code."""
        if not self.stderr_path.exists():
            return ""
        return self.stderr_path.read_bytes()[-max_bytes:].decode("utf-8", errors="replace")

    def start(self) -> None:
        if self._proc is not None:
            raise RuntimeError("RingCapture already started")
        self.ring_dir.mkdir(parents=True, exist_ok=True)
        wrap = max(1, round(self.ring_seconds / self.segment_seconds))
        gop = max(1, round(self.segment_seconds * self.fps))

        segment_url = str(self.ring_dir / "seg_%03d.mp4")
        popen_cwd: str | None = None
        if self.preview_tcp_port is None:
            output_args = [
                "-f",
                "segment",
                "-segment_time",
                str(self.segment_seconds),
                "-segment_wrap",
                str(wrap),
                "-reset_timestamps",
                "1",
                "-segment_list",
                str(self.list_path),
                "-segment_list_type",
                "csv",
                "-segment_list_size",
                "0",
                "-t",
                str(self.max_runtime_seconds),
                segment_url,
            ]
        else:
            # tee's bracketed per-output option syntax uses ':' as a
            # separator, which collides with a Windows drive letter
            # ("C:\...") and mangles backslashes - confirmed the hard way,
            # not a hypothetical. Sidestep it entirely: run ffmpeg with its
            # cwd set to ring_dir and use bare relative filenames in the
            # tee spec, matching what actually works everywhere else.
            popen_cwd = str(self.ring_dir)
            segment_spec = (
                f"[f=segment:segment_time={self.segment_seconds}:segment_wrap={wrap}"
                f":reset_timestamps=1:segment_list=list.csv"
                f":segment_list_type=csv:segment_list_size=0]seg_%03d.mp4"
            )
            # No listen=1 here - ffmpeg connects out as a client to an
            # already-listening viewer (drivers/cli/wall.py's ordering);
            # onfail=ignore means a viewer that isn't there yet, or that
            # goes away, only drops this slave, never the ring alongside
            # it. timeout bounds a *failed* connection attempt to 3s -
            # confirmed the default is much worse (ffmpeg's own log:
            # "after a lag of 4.929s"), stalling the ring, not just the
            # preview, for however long a connect attempt takes to give up.
            # WallDriver.start() awaiting the relay's own asyncio listener
            # coming up before starting the ring is what keeps this
            # timeout from being hit in practice; this is just the
            # backstop.
            preview_spec = f"[f=mpegts:onfail=ignore]tcp://127.0.0.1:{self.preview_tcp_port}?timeout=3000000"
            output_args = [
                "-t",
                str(self.max_runtime_seconds),
                "-f",
                "tee",
                "-map",
                "0:v",
                f"{segment_spec}|{preview_spec}",
            ]

        # x264 sends SPS/PPS once, at the very start of the encode - fine
        # for the ring (each segment is its own closed, self-describing
        # MP4), fatal for a live UDP preview joining mid-stream, which
        # otherwise never receives them ("non-existing PPS 0 referenced",
        # confirmed reproducible: a preview joining after start decoded
        # zero frames, every time, until this was added). Repeats the
        # parameter sets with every keyframe instead of once.
        #
        # -tune zerolatency: without it, x264's default B-frames/lookahead
        # buffer tens of frames internally before emitting any output -
        # fine for a file encode, but it's the single biggest latency
        # contributor for the live preview tee'd off this same encode
        # (server/relay.py; PLAN.md §3.7), which shares this process with
        # the ring for exactly the reason documented on preview_tcp_port
        # above. Costs some compression efficiency on the ring's own
        # segments - acceptable, since a captured clip gets its own
        # separate, quality-focused re-encode at extraction time anyway
        # (capture/extract.py) and the ring itself is a short-lived,
        # auto-deleted buffer, not an archive.
        encoder_extra_args = (
            ["-tune", "zerolatency", "-x264-params", "repeat-headers=1"] if self.encoder == "libx264" else []
        )

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            *self.input_args,
            "-c:v",
            self.encoder,
            "-preset",
            self.preset,
            "-g",
            str(gop),
            "-keyint_min",
            str(gop),
            "-sc_threshold",
            "0",
            *encoder_extra_args,
            *output_args,
        ]
        # Own process group/session, independent of shell/shim wrapping, so
        # stop() can reliably reach every descendant, not just this PID.
        popen_kwargs: dict = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if _WINDOWS
            else {"start_new_session": True}
        )
        # A real file, not `subprocess.PIPE`: this process can run for up to
        # `max_runtime_seconds` (minutes), and an unread PIPE fills its OS
        # buffer and deadlocks ffmpeg once it does - a file has no such
        # ceiling and needs no draining thread. `-loglevel warning` keeps
        # this small in the steady state; `tail_stderr` is what a caller
        # reads back after a failed `wait_until_ready`.
        self._stderr_file = self.stderr_path.open("wb")
        self._proc = subprocess.Popen(
            cmd,
            cwd=popen_cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=self._stderr_file,
            **popen_kwargs,
        )
        self.t0 = time.monotonic()
        atexit.register(self.stop)

    def stop(self, timeout: float = 5.0) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            kill_tree(proc, timeout=timeout)
        stderr_file, self._stderr_file = self._stderr_file, None
        if stderr_file is not None:
            stderr_file.close()

    def __enter__(self) -> RingCapture:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def segments(self) -> list[Segment]:
        """Current ring contents.

        The segment list is append-only across ring wraps, so a filename can
        appear more than once; only the latest row for a given filename
        reflects what that file currently holds on disk.
        """
        by_name: dict[str, tuple[float, float]] = {}
        if not self.list_path.exists():
            return []
        with self.list_path.open(newline="") as f:
            for row in csv.reader(f):
                if not row:
                    continue
                name, start, end = row[0], float(row[1]), float(row[2])
                by_name[name] = (start, end)
        return sorted(
            (Segment(self.ring_dir / name, s, e) for name, (s, e) in by_name.items()),
            key=lambda seg: seg.start,
        )
