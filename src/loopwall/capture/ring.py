"""FFmpeg segment ring on tmpfs, rolling deletion; uvc and lavfi camera types.

Built at 0.1.0 build order step 6 (ROADMAP.md).
"""

from __future__ import annotations

import atexit
import csv
import os
import platform
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

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
    ) -> None:
        self.ring_dir = Path(ring_dir)
        self.input_args = input_args
        self.segment_seconds = segment_seconds
        self.ring_seconds = ring_seconds
        self.fps = fps
        self.encoder = encoder
        self.preset = preset
        self.max_runtime_seconds = max_runtime_seconds
        self._proc: subprocess.Popen | None = None
        self.t0: float | None = None

    @property
    def list_path(self) -> Path:
        return self.ring_dir / "list.csv"

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        if self._proc is not None:
            raise RuntimeError("RingCapture already started")
        self.ring_dir.mkdir(parents=True, exist_ok=True)
        wrap = max(1, round(self.ring_seconds / self.segment_seconds))
        gop = max(1, round(self.segment_seconds * self.fps))
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
            str(self.ring_dir / "seg_%03d.mp4"),
        ]
        # Own process group/session, independent of shell/shim wrapping, so
        # stop() can reliably reach every descendant, not just this PID.
        popen_kwargs: dict = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if _WINDOWS
            else {"start_new_session": True}
        )
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **popen_kwargs,
        )
        self.t0 = time.monotonic()
        atexit.register(self.stop)

    def stop(self, timeout: float = 5.0) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        if _WINDOWS:
            # taskkill /T kills the whole process tree, not just proc.pid -
            # see the class docstring for why a single-PID kill is not
            # sufficient here.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=timeout,
            )
            proc.wait(timeout=timeout)
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=timeout)
        except ProcessLookupError:
            pass

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
