import io
import subprocess
from pathlib import Path

from rich.console import Console

from loopwall.capture.ring import RingCapture
from loopwall.demo import _render, play_clip_loop, play_live_preview


def _ring(tmp_path: Path, preview_tcp_port: int | None = None) -> RingCapture:
    return RingCapture(
        ring_dir=tmp_path / "ring", input_args=[], ring_seconds=20.0, preview_tcp_port=preview_tcp_port
    )


def _rendered_text(group) -> str:
    console = Console(file=io.StringIO(), width=100, no_color=True)
    console.print(group)
    return console.file.getvalue()


def test_render_before_any_segments(tmp_path: Path) -> None:
    ring = _ring(tmp_path)
    ring.ring_dir.mkdir(parents=True)

    text = _rendered_text(_render(ring, clip_count=0, loop_start=None))

    assert "warming up" in text
    assert "0" in text  # clips captured


def test_render_shows_clip_count_and_controls(tmp_path: Path) -> None:
    ring = _ring(tmp_path)
    ring.ring_dir.mkdir(parents=True)
    (ring.ring_dir / "list.csv").write_text("seg_000.mp4,0.000000,2.000000\n")

    text = _rendered_text(_render(ring, clip_count=2, loop_start=0.0))

    assert "2" in text  # clips captured
    assert "space" in text
    assert "quit" in text
    assert "recording" in text


def test_play_live_preview_command(monkeypatch) -> None:
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    play_live_preview(17123)

    cmd = captured["cmd"]
    assert cmd[0] == "ffplay"
    assert "tcp://127.0.0.1:17123?listen=1" in cmd
    assert "-loop" not in cmd  # a live stream isn't a loopable file


def test_play_clip_loop_command_loops_forever(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    clip = tmp_path / "clip_001.mp4"
    play_clip_loop(clip)

    cmd = captured["cmd"]
    assert cmd[0] == "ffplay"
    assert str(clip) in cmd
    assert "-loop" in cmd and "0" in cmd
    assert "-autoexit" not in cmd  # would conflict with looping forever
