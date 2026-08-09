"""Real-ffmpeg tests for `capture/composite.py` - `WallDriver`/`test_wall_app.py`'s own
end-to-end render test only ever composites plain, audio-free `lavfi` captures, which
never exercises the specific failure this module's `loop`-filter design was built to
avoid (see `composite_grid`'s own docstring): a clip with both audio and video streams
broke `-stream_loop -1` in the field, against a real imported file - reproduced here
with a synthetic clip carrying an audio track, not the exact original file, but the same
structural feature (multiple streams per input) that triggered it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from loopwall.capture.composite import CompositeError, composite_grid
from loopwall.capture.extract import probe_duration

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _make_clip(path: Path, seconds: float, with_audio: bool = False) -> None:
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440"]
    cmd += ["-t", str(seconds), "-c:v", "libx264", "-preset", "veryfast"]
    if with_audio:
        cmd += ["-c:a", "aac"]
    cmd.append(str(path))
    subprocess.run(cmd, capture_output=True, check=True, timeout=30)


def test_composite_grid_two_silent_clips(tmp_path: Path) -> None:
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    _make_clip(a, 3.0)
    _make_clip(b, 1.5)
    out = tmp_path / "out.mp4"

    result = composite_grid([a, b], out)

    assert result == out
    assert out.is_file()
    assert probe_duration(out) == pytest.approx(3.0, abs=0.2)


def test_composite_grid_handles_a_clip_with_an_audio_track(tmp_path: Path) -> None:
    """The specific case that broke `-stream_loop -1` in the field (a multi-stream
    input) - this is the regression test for that fix.
    """
    silent, with_audio = tmp_path / "silent.mp4", tmp_path / "audio.mp4"
    _make_clip(silent, 3.0)
    _make_clip(with_audio, 2.0, with_audio=True)
    out = tmp_path / "out.mp4"

    result = composite_grid([silent, with_audio], out)

    assert result == out
    assert probe_duration(out) == pytest.approx(3.0, abs=0.2)


def test_composite_grid_single_clip_skips_xstack(tmp_path: Path) -> None:
    clip = tmp_path / "solo.mp4"
    _make_clip(clip, 1.0)
    out = tmp_path / "out.mp4"

    composite_grid([clip], out)

    assert probe_duration(out) == pytest.approx(1.0, abs=0.2)


def test_composite_grid_empty_list_raises(tmp_path: Path) -> None:
    with pytest.raises(CompositeError, match="nothing to composite"):
        composite_grid([], tmp_path / "unused.mp4")
