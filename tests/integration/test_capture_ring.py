"""`RingCapture.wait_until_ready`/`tail_stderr` against real ffmpeg - the mechanism
`WallDriver.switch_camera()` uses to probe a device before touching a working ring.

Not `hardware`-marked - the failure path deliberately never touches real capture
hardware either: `uvc`/`v4l2` reliably fails to open fast on both platforms this
project targets (unsupported input format on Windows ffmpeg builds; `/dev/video99`
doesn't exist on Linux) - confirmed directly, not assumed, before relying on it here.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from loopwall.capture.ring import RingCapture
from loopwall.monitor.camera import build_input_args

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def test_wait_until_ready_true_for_a_source_that_opens_fine(tmp_path: Path) -> None:
    ring = RingCapture(
        ring_dir=tmp_path / "ring",
        input_args=build_input_args("lavfi", None, 320, 240, 10, None),
        segment_seconds=1.0,
        ring_seconds=10.0,
        fps=10,
    )
    ring.start()
    try:
        assert ring.wait_until_ready(timeout=2.0) is True
        assert ring.is_running
    finally:
        ring.stop()


def test_wait_until_ready_false_for_a_device_that_fails_to_open(tmp_path: Path) -> None:
    ring = RingCapture(
        ring_dir=tmp_path / "ring",
        input_args=build_input_args("uvc", "/dev/video99", 320, 240, 10, None),
        segment_seconds=1.0,
        ring_seconds=10.0,
        fps=10,
    )
    ring.start()
    try:
        assert ring.wait_until_ready(timeout=2.0) is False
        assert not ring.is_running
        # The whole point of redirecting stderr to a file instead of DEVNULL:
        # a caller can actually say *why* it failed, not just that it did.
        assert ring.tail_stderr().strip() != ""
    finally:
        ring.stop()


def test_tail_stderr_is_empty_before_anything_has_run(tmp_path: Path) -> None:
    ring = RingCapture(ring_dir=tmp_path / "ring", input_args=[])
    assert ring.tail_stderr() == ""
