"""End-to-end monitor lifecycle against a synthetic `lavfi` source.

Not `hardware`-marked - unlike test_capture_poc.py, this never touches real
capture hardware, so it's safe to run anywhere ffmpeg is installed. It's
still skipped rather than `hardware`-gated when ffmpeg is missing (e.g.
today's CI - see HANDOFF.md's "still to do" note on a lavfi CI-facing path)
rather than failing the whole suite.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from loopwall.capture.ring import RingCapture
from loopwall.monitor import render
from loopwall.monitor.camera import build_input_args

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def test_monitor_runs_and_tears_down_cleanly(tmp_path: Path) -> None:
    input_args = build_input_args("lavfi", None, 320, 240, 10, None)
    ring = RingCapture(
        ring_dir=tmp_path / "ring",
        input_args=input_args,
        segment_seconds=1.0,
        ring_seconds=10.0,
        fps=10,
        max_runtime_seconds=15.0,  # hard ceiling regardless of test outcome
    )

    with ring:
        render.run(ring, refresh_hz=4.0, iterations=8)  # ~2 seconds

    assert not ring.is_running
