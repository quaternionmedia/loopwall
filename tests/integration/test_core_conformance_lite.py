"""Core conformance-lite: hand-authored events (not CliDriver) fed straight to
Core.handle_event against a real `lavfi` ring, replaying smoke.yaml's exact
shape - proves Core's own committed-op/session-artifact behavior
independent of the CliDriver that normally produces these events (that
coupling is what test_play_lifecycle.py covers instead).

Not `hardware`-marked - lavfi never touches real capture hardware, so this
is safe to run anywhere ffmpeg is installed; skipped, not failed, when it
isn't (matching test_monitor_lifecycle.py's convention).
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from loopwall.capture.ring import RingCapture
from loopwall.core import Core
from loopwall.core.scheduler import Scheduler
from loopwall.core.session import SessionArtifact
from loopwall.core.timeline import Timeline
from loopwall.core.tracks import TrackRegistry
from loopwall.ldp.models import CaptureCueEvent, TrackEvent, TransportEvent
from loopwall.monitor.camera import build_input_args

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")

# Scaled up from smoke.yaml's 120bpm so this finishes in seconds, not the
# 32s the real hardware gate (tests/integration/test_smoke.py, deferred)
# reserves for - same bar shape, just compressed.
_BPM = 600.0
_BEATS_PER_BAR = 4
_SECONDS_PER_BAR = _BEATS_PER_BAR * 60.0 / _BPM


def test_smoke_shaped_run_produces_exact_committed_ops_and_clips(tmp_path: Path) -> None:
    input_args = build_input_args("lavfi", None, 320, 240, 15, None)
    ring = RingCapture(
        ring_dir=tmp_path / "ring",
        input_args=input_args,
        segment_seconds=1.0,
        ring_seconds=30.0,
        fps=15,
        max_runtime_seconds=30.0,
    )
    session = SessionArtifact(tmp_path / "sessions", bpm=_BPM, beats_per_bar=_BEATS_PER_BAR, scenario="smoke")
    core = Core(
        timeline=Timeline(),
        tracks=TrackRegistry(),
        scheduler=Scheduler(),
        session=session,
        ring=ring,
        default_camera_id="sim0",
    )

    with ring:
        t0 = time.monotonic()

        def tick(bar: int) -> None:
            target = t0 + bar * _SECONDS_PER_BAR
            now = time.monotonic()
            if target > now:
                time.sleep(target - now)
            core.handle_event(
                TransportEvent(
                    playing=bar > 0, bpm=_BPM, bar=bar, beat=1.0, phase=0.0, beats_per_bar=_BEATS_PER_BAR
                )
            )

        tick(0)
        tick(1)
        tick(2)  # 4-bar take on track 1; capture window = bars 2..6
        core.handle_event(TrackEvent(track=1, state="recording", pattern=1))
        core.handle_event(CaptureCueEvent(track=1, pattern=1, cycle_start_bar=2, bars=4))
        for bar in range(3, 8):
            tick(bar)  # bar 6, with no directive of its own, closes track 1's take
        tick(8)  # 2-bar take on track 2
        core.handle_event(TrackEvent(track=2, state="recording", pattern=1))
        core.handle_event(CaptureCueEvent(track=2, pattern=1, cycle_start_bar=8, bars=2))
        for bar in range(9, 12):
            tick(bar)  # bar 10 closes track 2's take
        tick(12)  # track 1 drops out
        core.handle_event(TrackEvent(track=1, state="silent", pattern=None))
        tick(13)
        tick(14)  # and returns
        core.handle_event(TrackEvent(track=1, state="playing", pattern=1))
        tick(15)
        tick(16)

    assert [op.bar for op in core.scheduler.committed] == [2, 8, 12, 14]
    assert [op.kind for op in core.scheduler.committed] == ["add", "add", "remove", "add"]

    index = json.loads(session.index_path.read_text())
    assert len(index["clips"]) == 2
    clips_by_track = {clip["track"]: clip for clip in index["clips"]}
    assert clips_by_track[1]["bars"] == 4
    assert clips_by_track[2]["bars"] == 2
    for clip in index["clips"]:
        assert (session.dir / Path(clip["path"]).name).exists()
