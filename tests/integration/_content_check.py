"""Shared burned-timecode content check: does an extracted clip's first frame actually
match its expected `lavfi` window, independent of duration alone.

Factored out of `test_smoke.py` once `test_polymeter.py` needed the identical check -
real, non-trivial duplication (the windowed-comparison logic below, not "three similar
lines"), not premature abstraction. See `assert_clip_matches_its_window`'s own docstring
for the SSIM threshold's empirical calibration.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path


def _ssim(path_a: Path, path_b: Path) -> float:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path_a), "-i", str(path_b), "-lavfi", "ssim", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    match = re.search(r"All:(\d+\.\d+)", result.stderr)
    assert match, f"could not parse ssim output: {result.stderr}"
    return float(match.group(1))


_WINDOW_SECONDS = 0.34  # candidate search radius around t_a - see this function's own docstring
_WINDOW_STEP_SECONDS = 1 / 30  # one candidate per frame at the reference fps


def assert_clip_matches_its_window(clip_path: Path, width: int, height: int, fps: int, t_a: float) -> None:
    """Independently re-render `testsrc2` near ring-relative time `t_a` (fresh, one-shot
    decodes - not reusing the ring's own stream at all) and compare against the extracted
    clip's own first frame. `testsrc2` isn't a monotonic clock signal - its own moving
    pattern can look similar at two different times - so this is a coarse sanity check,
    not a frame-exact proof. Calibrated empirically across smoke.yaml and polymeter.yaml's
    clips: a correct-window peak lands around 0.90-0.93 SSIM but can run as low as ~0.896
    depending on what the testsrc2 pattern looks like at that particular instant, while a
    wrong-time candidate - even one only a few hundred ms off - tops out around 0.78 in the
    worst case observed and mostly sits in the 0.6-0.75 range. The threshold below (0.85)
    sits in the gap between those two bands, not at either one's edge.

    Checks a small window of candidate reference frames around `t_a`, not just the exact
    nominal instant, and takes the best score - not a retry (the caller's own test still
    runs exactly once; nothing here re-runs a failed assertion hoping for a better roll).
    This accounts deterministically for a real, already-documented, bounded characteristic
    of this system: HANDOFF.md's "Duration compensator" entry measured ~100ms-scale timing
    variance between a requested extraction window and what the ring actually delivers,
    parked as a real, unexplained-but-bounded finding, not a wrong-window bug. `t_a` is
    also typically the *caller's own* nominal bar-time computation (`bar *
    seconds_per_bar`), not the driver's actual observed-timeline breakpoints, which
    compounds the same small-scale gap. `+/-0.34s` (~10 frames at 30fps) comfortably
    covers that known jitter while staying far short of the multi-second gap a genuinely
    wrong-window extraction would produce - a true bug would still fail every candidate in
    this narrow a window, landing in the 0.6-0.78 band above, not just under 0.90.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        actual = tmp / "actual.png"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "warning",
                "-y",
                "-i",
                str(clip_path),
                "-frames:v",
                "1",
                "-update",
                "1",
                str(actual),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )

        radius = round(_WINDOW_SECONDS / _WINDOW_STEP_SECONDS)
        offsets = [i * _WINDOW_STEP_SECONDS for i in range(-radius, radius + 1)]
        best_score = -1.0
        best_offset = 0.0
        for offset in offsets:
            candidate_t = max(0.0, t_a + offset)
            reference = tmp / f"reference_{offset:+.3f}.png"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "warning",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"testsrc2=size={width}x{height}:rate={fps}",
                    "-ss",
                    f"{candidate_t:.6f}",
                    "-frames:v",
                    "1",
                    "-update",
                    "1",
                    str(reference),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
            score = _ssim(actual, reference)
            if score > best_score:
                best_score, best_offset = score, offset

    assert best_score > 0.85, (
        f"{clip_path.name} doesn't match any fresh render within +/-{_WINDOW_SECONDS:.2f}s of its "
        f"expected window (t_a={t_a:.2f}s, best ssim={best_score:.3f} at offset={best_offset:+.3f}s) "
        f"- possible wrong-window extraction"
    )
