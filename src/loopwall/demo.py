"""loopwall demo: live preview, space to capture-and-loop. See PLAN.md section 3.7.

A `monitor` (PLAN.md section 3.6) is explicitly read-only; this module adds
the one write action loopwall has at this stage (trigger a capture) plus
playback, so it lives here rather than in `monitor/`. Bypasses LDP/core/the
driver entirely - see the module's PLAN.md section for what this does and
doesn't prove.

Interaction model: a live preview window shows the camera feed from the
moment recording starts. Pressing space cuts everything captured since the
last press (or since the start, the first time) into a clip and opens it in
its own looping window - "the first recording" becomes a loop, and the ring
keeps recording, uninterrupted, into what becomes the next one. 'q' quits.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from loopwall.capture.extract import ExtractionError, extract_window
from loopwall.capture.ring import RingCapture
from loopwall.keypress import RawMode, poll_key
from loopwall.monitor import render as monitor_render
from loopwall.monitor.state import capture_monitor_state
from loopwall.proctree import kill_tree

MIN_LOOP_SECONDS = 1.0  # ignore a space press before there's at least this much to cut


def play_live_preview(tcp_port: int) -> subprocess.Popen:
    """Listen for the tee'd live-preview stream - the camera feed, near-live, no camera re-open.

    Listens (`?listen=1`) rather than connecting out - `RingCapture` (see
    its own docstring) connects to this as a client once it starts, and
    must never be blocked waiting for a viewer, so the viewer has to be
    the one waiting here instead. Caller must spawn this and give it a
    moment to actually bind before starting the ring - see `run()`.
    """
    return subprocess.Popen(
        [
            "ffplay",
            "-loglevel",
            "error",
            "-window_title",
            "loopwall - live",
            f"tcp://127.0.0.1:{tcp_port}?listen=1",
        ],
        stdin=subprocess.DEVNULL,
    )


def play_clip_loop(path: Path) -> subprocess.Popen:
    """Open a captured clip in its own window, looping forever until closed by hand."""
    return subprocess.Popen(
        [
            "ffplay",
            "-loop",
            "0",
            "-loglevel",
            "error",
            "-window_title",
            f"loopwall - {Path(path).stem}",
            str(path),
        ],
        stdin=subprocess.DEVNULL,
    )


def _is_listening(port: int) -> bool:
    """True if something is bound and LISTENING on 127.0.0.1:port right now.

    Deliberately *not* a connect probe: `ffplay ... ?listen=1` accepts
    exactly one client, so a probe connection would consume that accept
    and leave nothing for the ring's real connection afterward. Shelling
    out to the OS's own listening-socket table sidesteps that entirely.
    Best-effort - a platform without netstat/ss just always reports False,
    which only costs the (bounded, see RingCapture's tcp `timeout`) slow
    path, never correctness.
    """
    needle = f":{port}"
    try:
        if sys.platform == "win32":
            out = subprocess.run(["netstat", "-an"], capture_output=True, text=True, timeout=2).stdout
            return any(needle in line and "LISTENING" in line for line in out.splitlines())
        out = subprocess.run(["ss", "-ltn"], capture_output=True, text=True, timeout=2).stdout
        return any(needle in line for line in out.splitlines())
    except (OSError, subprocess.SubprocessError):
        return False


def _wait_for_listening(port: int, timeout: float = 5.0, poll_interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_listening(port):
            return True
        time.sleep(poll_interval)
    return False


def _render(ring: RingCapture, clip_count: int, loop_start: float | None) -> Group:
    capture_state = capture_monitor_state(ring)
    cap = capture_state.capture
    loop_elapsed = None
    if loop_start is not None and cap is not None and cap.ring_end is not None:
        loop_elapsed = cap.ring_end - loop_start

    status = Table.grid(padding=(0, 2))
    status.add_column(justify="right", style="dim")
    status.add_column()
    if loop_elapsed is None:
        status.add_row("status", "warming up - waiting for the first segment...")
    else:
        status.add_row("status", f"recording... {loop_elapsed:.1f}s so far")
    status.add_row("clips captured", str(clip_count))
    # Rich console markup treats [...] as style tags; plain brackets around
    # key names get silently swallowed rather than shown - parentheses avoid
    # the whole ambiguity instead of fighting markup escaping for a label.
    status.add_row("controls", "(space) capture & loop it    (q) quit")

    return Group(monitor_render.render(capture_state), Panel(status, title="demo", border_style="magenta"))


def run(
    ring: RingCapture,
    session_dir: Path,
    refresh_hz: float = 4.0,
    preview_player: Callable[[int], subprocess.Popen] = play_live_preview,
    loop_player: Callable[[Path], subprocess.Popen] = play_clip_loop,
    console: Console | None = None,
    preview_ready_timeout: float = 5.0,
) -> int:
    """Run the live-preview + space-to-capture loop until 'q'. Returns clips captured.

    Owns `ring`'s full start/stop lifecycle itself rather than expecting an
    already-started ring - the preview must be listening *before* the ring
    starts (its tee connects out as a client; see `RingCapture`'s own
    docstring for why that ordering, not the reverse, is load-bearing), so
    this function is the one place that ordering can actually be
    guaranteed. Pass `ring` unstarted.

    Confirmed the hard way that "spawn the preview, sleep a bit, start the
    ring" isn't good enough: a fixed 0.5s delay was nowhere near how long
    `ffplay` (through a launcher shim, same one from the capture POC)
    actually takes to bind its listener, and a *failed* connection attempt
    doesn't fail fast - it stalled the ring for ~5s too, confirmed via
    ffmpeg's own log ("Resumed reading... after a lag of 4.929s"). Polls
    the OS's listening-socket table instead of guessing a delay or
    connecting a probe (which would consume the listener's one-shot
    accept); `RingCapture`'s own bounded connect timeout is the backstop
    if this never resolves.

    `preview_player`/`loop_player` are injectable so this can be tested
    without spawning real video player windows - see
    tests/integration/test_demo_lifecycle.py. Requires `ring` to have been
    constructed with `preview_tcp_port` set.
    """
    if ring.preview_tcp_port is None:
        raise ValueError("demo.run() requires a RingCapture constructed with preview_tcp_port set")
    if ring.is_running:
        raise ValueError("demo.run() starts the ring itself - pass it unstarted")

    session_dir = Path(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)
    console = console or Console()

    preview_proc = preview_player(ring.preview_tcp_port)
    _wait_for_listening(ring.preview_tcp_port, timeout=preview_ready_timeout)
    ring.start()

    loop_start: float | None = None
    clip_count = 0

    try:
        with (
            RawMode(),
            Live(
                _render(ring, clip_count, loop_start), console=console, refresh_per_second=refresh_hz
            ) as live,
        ):
            while True:
                key = poll_key()
                if key is not None:
                    key = key.lower()
                    if key == "q":
                        break
                    if key == " ":
                        segs = ring.segments()
                        if segs:
                            t_b = segs[-1].end
                            t_a = loop_start if loop_start is not None else segs[0].start
                            if t_b - t_a >= MIN_LOOP_SECONDS:
                                out_path = session_dir / f"clip_{clip_count + 1:03d}.mp4"
                                try:
                                    extract_window(ring, t_a, t_b, out_path)
                                except ExtractionError:
                                    pass
                                else:
                                    clip_count += 1
                                    loop_player(out_path)
                                    loop_start = t_b
                time.sleep(1.0 / refresh_hz)
                live.update(_render(ring, clip_count, loop_start))
    finally:
        ring.stop()
        kill_tree(preview_proc)

    return clip_count
