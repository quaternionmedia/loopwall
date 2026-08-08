"""Overlapping, differently-sized captures: `scenarios/polymeter.yaml`.

`scenarios/smoke.yaml` never has two tracks recording at once - its takes
are sequential (track 1 finishes at bar 6 before track 2 starts at bar 8).
That leaves a real gap: does the ring's per-track pending-capture bookkeeping
(`Core._pending`, keyed per track with its own `close_bar`) actually keep two
concurrent, differently-lengthed windows straight, or can a second capture's
completion clobber or misattribute the first's extraction window?

`polymeter.yaml` starts a 3-bar track-1 take at bar 2 and, one bar later,
starts a 4-bar track-2 take at bar 3 - both open at once from bar 3 to bar 5.
Each still needs to extract only its own footage (verified via the same
burned-timecode SSIM check `test_smoke.py` uses, imported from
`_content_check.py` rather than duplicated - see that module's docstring).
It also re-uses track 1's silent/playing return (bars 9/11, after both
captures have long since completed) to confirm the presence-flip op rule
still holds once a track has a completed take sitting on the wall next to a
still-open second track - not just in the simpler sequential-takes case
`smoke.yaml` covers.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from _content_check import assert_clip_matches_its_window
from click.testing import CliRunner

from loopwall.__main__ import cli
from loopwall.core.session import SessionIndex
from loopwall.ldp.wire import read_jsonl

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")

POLYMETER_SCENARIO = Path(__file__).resolve().parents[2] / "scenarios" / "polymeter.yaml"
_BPM = 120.0
_BEATS_PER_BAR = 4
_SECONDS_PER_BAR = _BEATS_PER_BAR * 60.0 / _BPM  # 2.0s/bar
_FRAME_SECONDS = 1 / 30
_SEGMENT_SECONDS = 2.0
_RING_SECONDS = 15.0  # > the longest single take (4 bars = 8s)
_EXPECT_CLIPS = [
    {"track": 1, "pattern": 1, "bars": 3, "cycle_start_bar": 2},
    {"track": 2, "pattern": 1, "bars": 4, "cycle_start_bar": 3},
]
_EXPECT_COMMITTED_OPS_ON_BARS = [2, 3, 9, 11]


def _write_config(config_path: Path, ring_dir: Path, session_root: Path, camera: dict) -> None:
    config = {
        "driver": {"type": "cli"},
        "capture": {
            "cameras": [camera],
            "ring": {"dir": str(ring_dir), "seconds": _RING_SECONDS},
            "encoder": "x264",
        },
        "session_root": str(session_root),
        "server": {"headless": True},
    }
    config_path.write_text(yaml.safe_dump(config))


def _run_play(config_path: Path) -> Path:
    result = CliRunner().invoke(cli, ["play", str(POLYMETER_SCENARIO), "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    return Path(result.output.strip())


def test_overlapping_captures_extract_their_own_windows(tmp_path: Path) -> None:
    width, height, fps = 320, 240, 30
    camera = {"id": "sim0", "type": "lavfi", "res": f"{width}x{height}", "fps": fps}

    config = tmp_path / "config.yaml"
    _write_config(config, tmp_path / "ring", tmp_path / "sessions", camera)
    session_dir = _run_play(config)

    index = SessionIndex.model_validate_json((session_dir / "index.json").read_text())
    assert len(index.clips) == len(_EXPECT_CLIPS)
    clips_by_track = {clip.track: clip for clip in index.clips}
    assert set(clips_by_track) == {e["track"] for e in _EXPECT_CLIPS}

    for expect in _EXPECT_CLIPS:
        clip = clips_by_track[expect["track"]]
        assert clip.pattern == expect["pattern"]
        assert clip.bars == expect["bars"]
        assert (session_dir / Path(clip.path).name).exists()

        expected_duration = expect["bars"] * _SECONDS_PER_BAR
        assert abs(clip.duration_seconds - expected_duration) <= _FRAME_SECONDS, (
            f"track {expect['track']}: expected ~{expected_duration:.3f}s, got {clip.duration_seconds:.3f}s"
        )
        assert clip.extraction_latency_ms <= 3000

    # The real point of this scenario: with both captures open at once
    # (bars 3-5), each clip must still match *its own* window, not the
    # other track's overlapping one.
    for expect in _EXPECT_CLIPS:
        clip = clips_by_track[expect["track"]]
        t_a = expect["cycle_start_bar"] * _SECONDS_PER_BAR
        assert_clip_matches_its_window(session_dir / Path(clip.path).name, width, height, fps, t_a)

    # Committed-op bars: presence flips at 2 (track1 add) and 3 (track2 add)
    # despite the overlap, nothing fires when either capture completes
    # (recording -> playing is presence-preserving), and the later
    # silent/playing return on track 1 (bars 9/11) still flips cleanly with
    # track 2's take sitting on the wall next to it the whole time.
    log_records = read_jsonl(session_dir / "log.jsonl")
    logged_op_bars = [record["data"]["bar"] for record in log_records if record["kind"] == "committed_op"]
    assert logged_op_bars == _EXPECT_COMMITTED_OPS_ON_BARS

    replay_result = CliRunner().invoke(cli, ["replay", str(session_dir)])
    assert replay_result.exit_code == 0, replay_result.output
    assert "OK" in replay_result.output
