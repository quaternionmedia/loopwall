"""Realized-timestamp window extraction, x264 re-encode to exact cycle duration.

Built at 0.1.0 build order step 7 (ROADMAP.md).
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from loopwall.capture.ring import RingCapture


class ExtractionError(RuntimeError):
    pass


def extract_window(
    ring: RingCapture,
    t_a: float,
    t_b: float,
    out_path: Path,
    encoder: str = "libx264",
    preset: str = "veryfast",
) -> Path:
    """Cut ``[t_a, t_b)`` of ring-relative time to an exact-duration clip.

    PLAN.md §3.4: cuts on realized bar timestamps, re-encoded once to exact
    cycle duration. ``t_a``/``t_b`` are seconds against ``ring``'s own
    stream clock; production maps bar boundaries onto that clock via the
    shared monotonic timeline (``core/timeline.py``).
    """
    if t_b <= t_a:
        raise ValueError(f"t_b ({t_b}) must be after t_a ({t_a})")

    covering = [s for s in ring.segments() if s.end > t_a and s.start < t_b]
    if not covering:
        raise ExtractionError(f"no ring segments cover [{t_a}, {t_b})")
    if covering[0].start > t_a or covering[-1].end < t_b:
        raise ExtractionError(
            f"window [{t_a}, {t_b}) not fully covered by the current ring "
            f"(has [{covering[0].start}, {covering[-1].end}) - already overwritten?)"
        )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", suffix=".txt", dir=ring.ring_dir, delete=False) as f:
        for seg in covering:
            f.write(f"file '{seg.path.name}'\n")
        concat_list = Path(f.name)

    try:
        in_point = t_a - covering[0].start
        out_point = t_b - covering[0].start
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-ss",
            f"{in_point:.6f}",
            "-to",
            f"{out_point:.6f}",
            "-c:v",
            encoder,
            "-preset",
            preset,
            "-movflags",
            "+faststart",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise ExtractionError(f"ffmpeg extraction failed: {result.stderr}")
    finally:
        concat_list.unlink(missing_ok=True)

    return out_path


def wait_for_coverage(ring: RingCapture, t_b: float, timeout: float = 5.0) -> bool:
    """Block until the ring has flushed a segment covering up to t_b.

    A requested window can ask for slightly more than the ring has
    registered yet (segments only appear once a full segment_seconds chunk
    closes) - give it a moment to catch up rather than racing it with
    extract_window directly. Returns False on timeout rather than raising;
    callers still get ExtractionError from extract_window itself if the
    window genuinely isn't coverable.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        segs = ring.segments()
        if segs and segs[-1].end >= t_b:
            return True
        time.sleep(0.2)
    return False


def probe_duration(path: Path) -> float:
    """ffprobe's own measured duration of a clip, in seconds."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    return float(result.stdout.strip())


@dataclass
class DurationCompensator:
    """Rolling correction for a systematic extraction-duration bias.

    PROVISIONAL — a stop-gap, not a fix. The capture POC (see HANDOFF.md's
    "Capture/extraction proof of concept" note) measured a consistent
    ~100ms undershoot (a 4.000s request came back 3.900s, 3x repeated),
    most likely GOP/segment-boundary rounding — the retrospective's own
    falsifiable prediction #2
    (``perspectives/2026-07-16-plan-retrospective.md``). Root-causing that
    is explicitly deferred; this class exists so *subsequent* extractions
    in a session can self-correct in the meantime rather than each one
    independently eating the same bias. Flagged for review and replacement
    (or removal, once root-caused) at a later milestone — not a ratified
    design, no ADR.

    Usage: call :meth:`compensate` on a requested ``(t_a, t_b)`` before
    calling :func:`extract_window`, then :meth:`observe` with the extracted
    clip's actual duration (e.g. via ffprobe) to update the correction for
    the *next* call. A fresh compensator starts uncorrected (bias 0.0), so
    the first extraction in a session is never adjusted — only ones after
    it that have an observed bias to react to.
    """

    bias: float = 0.0  # last observed (actual - requested) duration, seconds
    alpha: float = 1.0  # correction weight; 1.0 fully cancels the last bias

    def compensate(self, t_a: float, t_b: float) -> tuple[float, float]:
        """Return a ``(t_a, t_b)`` adjusted to counteract the last observed bias.

        Extends/shrinks the window from its end (``t_b``) rather than its
        start, matching PLAN.md §3.4's cut convention (a window begins at a
        cycle boundary; only its far edge is negotiable).
        """
        return t_a, t_b - (self.bias * self.alpha)

    def observe(self, requested_duration: float, actual_duration: float) -> None:
        self.bias = actual_duration - requested_duration
