"""Milestone-1 integration gate, verbatim (ROADMAP.md's own numbered assertions).

Real hardware, on a development machine with any UVC/dshow webcam:

    uv run pytest -m hardware tests/integration/test_smoke.py -v

This module's default (non-`hardware`) tests are the CI twin: substitutes
`camera: lavfi` for real hardware, and - since a synthetic source makes it
possible - additionally verifies each clip's actual pixel content against an
independently-rendered reference frame at the same ring-relative time (SSIM),
not just its duration. That's what ROADMAP.md's "asserting burned-timecode
windows" is checking for: a wrong-by-N-seconds extraction can still land on
the *right* duration, so duration alone can't tell the two apart the way an
independent content re-render can (PLAN.md §3.4: "a `lavfi` synthetic camera
... is a first-class camera type, used by CI to assert extraction windows
without hardware"). See `_content_check.py` for the comparison itself and
its calibration notes; `test_polymeter.py` shares the identical check.

Runs `scenarios/smoke.yaml` unmodified at its authored 120bpm (~32s real time
per run, ~65s total across the two runs assertion 6 needs) - the gate text
only substitutes camera type for the CI twin, not timing, so this doesn't
compress tempo the way `test_core_conformance_lite.py` (same bar shape,
hand-authored events straight into `Core`, not the real CLI path) does.
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

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")

SMOKE_SCENARIO = Path(__file__).resolve().parents[2] / "scenarios" / "smoke.yaml"
_BPM = 120.0
_BEATS_PER_BAR = 4
_SECONDS_PER_BAR = _BEATS_PER_BAR * 60.0 / _BPM  # 2.0s/bar
_FRAME_SECONDS = 1 / 30  # ROADMAP.md's own stated tolerance unit (33ms at 30fps)
_SEGMENT_SECONDS = 2.0  # build_ring_from_config's default
_RING_SECONDS = 15.0  # > the longest single take (8s) so nothing in flight gets pruned early
_EXPECT_CLIPS = [
    {"track": 1, "pattern": 1, "bars": 4, "cycle_start_bar": 2},
    {"track": 2, "pattern": 1, "bars": 2, "cycle_start_bar": 8},
]


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
    result = CliRunner().invoke(cli, ["play", str(SMOKE_SCENARIO), "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    return Path(result.output.strip())


def _assert_gate_1_through_5(session_dir: Path, ring_dir: Path) -> SessionIndex:
    # 1. Session directory: log.jsonl, one clip per record directive, schema-valid index.json.
    assert (session_dir / "log.jsonl").exists()
    index = SessionIndex.model_validate_json((session_dir / "index.json").read_text())
    assert len(index.clips) == len(_EXPECT_CLIPS)
    clips_by_track = {clip.track: clip for clip in index.clips}
    assert set(clips_by_track) == {e["track"] for e in _EXPECT_CLIPS}
    assert index.active_takes == {f"{e['track']}:{e['pattern']}": 1 for e in _EXPECT_CLIPS}

    for expect in _EXPECT_CLIPS:
        clip = clips_by_track[expect["track"]]
        assert clip.pattern == expect["pattern"]
        assert clip.bars == expect["bars"]
        assert (session_dir / Path(clip.path).name).exists()

        # 2. Duration equals bars * beats_per_bar * 60/bpm within +/-1 frame at 30fps.
        expected_duration = expect["bars"] * _SECONDS_PER_BAR
        assert abs(clip.duration_seconds - expected_duration) <= _FRAME_SECONDS, (
            f"track {expect['track']}: expected ~{expected_duration:.3f}s, got {clip.duration_seconds:.3f}s"
        )

        # 3. Extraction latency logged and <=3s.
        assert clip.extraction_latency_ms <= 3000

    # 4. `loopwall replay <session>` reproduces an identical committed-op sequence.
    replay_result = CliRunner().invoke(cli, ["replay", str(session_dir)])
    assert replay_result.exit_code == 0, replay_result.output
    assert "OK" in replay_result.output

    # 5. The ring directory is bounded for the whole run (rolling deletion, not growth).
    wrap = max(1, round(_RING_SECONDS / _SEGMENT_SECONDS))
    unbounded_would_be = round((16 * _SECONDS_PER_BAR) / _SEGMENT_SECONDS)  # smoke.yaml's full 16-bar span
    segment_files = list(ring_dir.glob("seg_*.mp4"))
    assert len(segment_files) <= wrap + 1, (
        f"ring grew past its {wrap}-segment cap: {len(segment_files)} files"
    )
    assert len(segment_files) < unbounded_would_be, "ring shows no sign of rolling deletion at all"

    return index


def test_ci_twin_lavfi_smoke_gate(tmp_path: Path) -> None:
    width, height, fps = 320, 240, 30
    camera = {"id": "sim0", "type": "lavfi", "res": f"{width}x{height}", "fps": fps}

    config_1 = tmp_path / "config1.yaml"
    _write_config(config_1, tmp_path / "ring1", tmp_path / "sessions", camera)
    session_1 = _run_play(config_1)

    index = _assert_gate_1_through_5(session_1, tmp_path / "ring1")

    # Burned-timecode window check (lavfi-only - see module docstring).
    clips_by_track = {clip.track: clip for clip in index.clips}
    for expect in _EXPECT_CLIPS:
        clip = clips_by_track[expect["track"]]
        t_a = expect["cycle_start_bar"] * _SECONDS_PER_BAR
        assert_clip_matches_its_window(session_1 / Path(clip.path).name, width, height, fps, t_a)

    # 6. Re-running the scenario produces a new session; nothing in the old one is touched.
    snapshot = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in session_1.rglob("*") if p.is_file()}

    config_2 = tmp_path / "config2.yaml"
    _write_config(config_2, tmp_path / "ring2", tmp_path / "sessions", camera)
    session_2 = _run_play(config_2)

    assert session_2 != session_1
    for path, (mtime_ns, content) in snapshot.items():
        assert path.stat().st_mtime_ns == mtime_ns, f"{path} was touched by the second run"
        assert path.read_bytes() == content, f"{path}'s content changed after the second run"


@pytest.mark.hardware
def test_real_hardware_smoke_gate(tmp_path: Path) -> None:
    """The gate as ROADMAP.md states it, verbatim: real UVC/dshow webcam, no lavfi.

    Not run automatically anywhere in this session - real camera hardware,
    ~65s of real capture. Run by hand: `uv run pytest -m hardware
    tests/integration/test_smoke.py::test_real_hardware_smoke_gate -v`.
    """
    from loopwall.monitor.camera import detect_default_camera

    camera_type, device = detect_default_camera()
    if camera_type == "lavfi":
        pytest.skip("no real camera detected on this machine")

    camera = {"id": "cam0", "type": camera_type, "device": device, "res": "1280x720", "fps": 30}

    config_1 = tmp_path / "config1.yaml"
    _write_config(config_1, tmp_path / "ring1", tmp_path / "sessions", camera)
    session_1 = _run_play(config_1)

    _assert_gate_1_through_5(session_1, tmp_path / "ring1")

    snapshot = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in session_1.rglob("*") if p.is_file()}

    config_2 = tmp_path / "config2.yaml"
    _write_config(config_2, tmp_path / "ring2", tmp_path / "sessions", camera)
    session_2 = _run_play(config_2)

    assert session_2 != session_1
    for path, (mtime_ns, content) in snapshot.items():
        assert path.stat().st_mtime_ns == mtime_ns, f"{path} was touched by the second run"
        assert path.read_bytes() == content, f"{path}'s content changed after the second run"
