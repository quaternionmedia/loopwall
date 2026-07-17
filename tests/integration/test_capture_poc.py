"""Proof of concept: real-webcam ring capture + after-the-fact window extraction.

Retires the project's genuinely novel risk (PLAN.md's own retrospective,
`perspectives/2026-07-16-plan-retrospective.md`): can a window be cut
accurately out of a continuously-recording ring after the fact - and, given
the POC found a systematic ~100ms undershoot rather than the plan's
±1-frame target, whether a same-session compensator (`DurationCompensator`
in `src/loopwall/capture/extract.py`) can correct *subsequent* extractions
in the meantime, ahead of root-causing the bias itself. Marked `hardware` -
skipped in CI, run manually:

    uv run pytest -m hardware tests/integration/test_capture_poc.py -v

Requires a real camera. On Windows this uses ffmpeg's dshow input; set
LOOPWALL_POC_DSHOW_DEVICE to override the default device name (list yours
with `ffmpeg -f dshow -list_devices true -i dummy`). Production capture
targets UVC/V4L2 on Linux (PLAN.md §3.4; config.example.yaml); dshow here is
a Windows dev-machine convenience for this proof of concept only.

Camera safety: RingCapture is always used as a context manager so ffmpeg is
terminated in a `finally` (via __exit__) even on assertion failure, AND
ffmpeg itself is started with a hard `-t` runtime cap, so a capture can
never outlive the test even if teardown is somehow skipped. See
`src/loopwall/capture/ring.py`'s own docstring for the full guarantee.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from loopwall.capture.extract import DurationCompensator, extract_window, probe_duration, wait_for_coverage
from loopwall.capture.ring import RingCapture

pytestmark = pytest.mark.hardware

DSHOW_DEVICE = os.environ.get("LOOPWALL_POC_DSHOW_DEVICE", "Camera Sensor OV02C10")
FRAME_SECONDS = 1 / 30


@pytest.fixture
def ring_capture(tmp_path: Path):
    cap = RingCapture(
        ring_dir=tmp_path / "ring",
        input_args=[
            "-f",
            "dshow",
            "-video_size",
            "1280x720",
            "-framerate",
            "30",
            "-i",
            f"video={DSHOW_DEVICE}",
        ],
        segment_seconds=2.0,
        ring_seconds=30.0,
        fps=30,
        max_runtime_seconds=40.0,  # hard ceiling regardless of test outcome
    )
    with cap:
        yield cap
    # Belt-and-braces: confirm ffmpeg is actually gone, not just asked to stop.
    assert not cap.is_running, "ffmpeg did not exit after RingCapture.stop()"


def _extract_and_measure(
    ring_capture: RingCapture, t_a: float, t_b: float, out_path: Path
) -> tuple[float, float]:
    """Extract [t_a, t_b), return (requested_duration, actual_duration)."""
    extract_window(ring_capture, t_a, t_b, out_path)
    assert out_path.exists()
    return t_b - t_a, probe_duration(out_path)


def test_duration_compensator_wires_into_subsequent_loops(ring_capture: RingCapture, tmp_path: Path) -> None:
    """Two sequential "loop" extractions from the same session.

    The first is uncompensated (a fresh DurationCompensator starts at bias
    0.0); the second's request is adjusted by what the first one observed.
    This exercises the mechanism end to end and *measures* whether it
    helps - it does not assert that it does. Two real runs gave two
    different outcomes: once loop 2 came back ~100ms short too, same as
    loop 1, despite asking for 100ms more (no correction at all); another
    time it overshot by ~100ms in the *other* direction. Read together,
    that rules out "the output is always exactly N ms short of `-to`" (a
    fixed additive bias a linear ask-for-more would reliably close) -
    whatever's quantizing the cut varies enough between runs that this
    stop-gap can't be tuned to a single constant. A real, useful negative
    result for the later investigation this is explicitly flagged for
    (HANDOFF.md's "Capture/extraction proof of concept" note), not a
    reason to keep tuning the compensator blind here.
    """
    compensator = DurationCompensator()

    # Loop 1: uncompensated.
    time.sleep(10)
    segs = ring_capture.segments()
    assert len(segs) >= 2, "expected at least two ring segments after 10s"
    t_b = segs[-1].end
    t_a = t_b - 4.0
    assert t_a >= segs[0].start, "requested window predates the current ring"

    requested_1, actual_1 = _extract_and_measure(ring_capture, t_a, t_b, tmp_path / "loop1.mp4")
    delta_1 = actual_1 - requested_1
    compensator.observe(requested_1, actual_1)
    print(f"loop 1 (uncompensated): actual={actual_1:.3f}s delta={delta_1 * 1000:+.1f}ms")

    # Loop 2: same nominal 4s request, adjusted by loop 1's observed bias.
    time.sleep(6)
    segs = ring_capture.segments()
    t_b2 = segs[-1].end
    t_a2 = t_b2 - 4.0
    assert t_a2 >= segs[0].start, "requested window predates the current ring"
    t_a2, t_b2 = compensator.compensate(t_a2, t_b2)
    if not wait_for_coverage(ring_capture, t_b2):
        raise TimeoutError(f"ring never caught up to cover t_b={t_b2}")

    requested_2, actual_2 = _extract_and_measure(ring_capture, t_a2, t_b2, tmp_path / "loop2.mp4")
    # "vs 4.0" here, not vs requested_2 - requested_2 is the *compensated*
    # ask; 4.0s is the nominal target both loops are actually chasing.
    delta_2 = actual_2 - 4.0
    print(f"loop 2 (compensated):   actual={actual_2:.3f}s delta={delta_2 * 1000:+.1f}ms")

    # Both extractions must succeed and land in the same ballpark as the
    # standalone POC (FRAME_SECONDS*5) - this is a measurement of the
    # compensator, not a claim that it corrects the bias yet. See the
    # test's own docstring for what the first real run actually showed.
    assert abs(delta_1) < FRAME_SECONDS * 5
    assert abs(delta_2) < FRAME_SECONDS * 5
