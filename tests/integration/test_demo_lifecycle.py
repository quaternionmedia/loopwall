"""End-to-end demo lifecycle against a synthetic `lavfi` source, tee'd preview included.

Not `hardware`-marked - never touches real capture hardware, so it's safe
anywhere ffmpeg is installed; skips cleanly (not a failure) when it's not,
same convention as test_monitor_lifecycle.py. `poll_key`/`RawMode` are
monkeypatched (not injected via a `run()` parameter - they're an ambient
input mechanism, not a per-call dependency, and `RawMode` would otherwise
try to manipulate a real tty that doesn't exist under pytest).
"""

from __future__ import annotations

import shutil
import socket
from pathlib import Path

import pytest

from loopwall import demo
from loopwall.capture.ring import RingCapture
from loopwall.monitor.camera import build_input_args

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


class _NoopRawMode:
    def __enter__(self) -> _NoopRawMode:
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass


def _scripted_keys(*keys: str | None):
    remaining = iter(keys)

    def poll() -> str | None:
        return next(remaining, "q")  # once the script runs out, quit

    return poll


def _ring(tmp_path: Path, preview_port: int) -> RingCapture:
    input_args = build_input_args("lavfi", None, 320, 240, 10, None)
    return RingCapture(
        ring_dir=tmp_path / "ring",
        input_args=input_args,
        segment_seconds=1.0,
        ring_seconds=10.0,
        fps=10,
        max_runtime_seconds=15.0,  # hard ceiling regardless of test outcome
        preview_tcp_port=preview_port,
    )


def test_demo_captures_one_loop_on_spacebar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Unstarted - demo.run() starts it itself (preview-before-ring ordering
    # is the whole point of that; see RingCapture's and run()'s docstrings).
    ring = _ring(tmp_path, preview_port=17201)
    monkeypatch.setattr(demo, "RawMode", _NoopRawMode)
    # ~30 ticks of None (let the ring warm up at refresh_hz=10), then space
    # (capture), then a few more None, then q.
    monkeypatch.setattr(demo, "poll_key", _scripted_keys(*([None] * 30), " ", *([None] * 5), "q"))

    preview_played: list[int] = []
    loop_played: list[Path] = []
    listener = _FakeListener(17201)

    try:
        clip_count = demo.run(
            ring,
            session_dir=tmp_path / "clips",
            refresh_hz=10.0,
            preview_player=lambda port: preview_played.append(port) or listener,
            loop_player=lambda path: loop_played.append(path) or _FakeProc(),
        )
    finally:
        listener.close()

    assert clip_count == 1
    assert preview_played == [17201]
    assert len(loop_played) == 1
    assert loop_played[0].exists()
    assert not ring.is_running


def test_demo_quits_immediately_without_capturing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ring = _ring(tmp_path, preview_port=17202)
    monkeypatch.setattr(demo, "RawMode", _NoopRawMode)
    monkeypatch.setattr(demo, "poll_key", _scripted_keys("q"))

    clip_count = demo.run(
        ring,
        session_dir=tmp_path / "clips",
        refresh_hz=10.0,
        preview_player=lambda port: _FakeProc(),
        loop_player=lambda path: _FakeProc(),
        preview_ready_timeout=0.3,
    )

    assert clip_count == 0
    assert not ring.is_running


class _FakeProc:
    """Stands in for subprocess.Popen - demo.run() only ever calls kill_tree() on the preview proc."""

    def poll(self) -> int:
        return 0  # already "exited" - kill_tree() treats this as nothing to do


class _FakeListener:
    """A real bound+listening TCP socket standing in for ffplay's listener.

    Not a real ffplay - but bind()+listen() alone is enough for
    demo._wait_for_listening's netstat check to find it, and for the
    ring's actual outbound TCP connect() to succeed (a completed 3-way
    handshake doesn't need anyone to have called accept() yet), so the
    ring takes the fast happy path instead of the ~3s connect-timeout
    backstop a truly absent listener would hit. poll() mimics an
    already-exited process so kill_tree() no-ops rather than trying to
    taskkill/killpg a PID that was never a real subprocess.
    """

    def __init__(self, port: int) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", port))
        self._sock.listen(1)

    def poll(self) -> int:
        return 0

    def close(self) -> None:
        self._sock.close()
