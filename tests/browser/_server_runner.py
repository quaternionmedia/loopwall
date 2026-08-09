"""Standalone entry point for `tests/browser/conftest.py`: builds and serves the wall app
in its own OS process, not a thread.

A background *thread* running its own `uvicorn`/asyncio event loop inside the same
process as pytest's own `anyio`-based async tests was tried first and reverted - on this
project's Windows dev environment it left the interpreter's asyncio state corrupted for
the rest of the test session (`RuntimeError: Cannot run the event loop while another loop
is running` in unrelated, later tests, deterministic and reproducible) even after the
thread's own server had shut down cleanly. A real subprocess sidesteps the question
entirely: separate process, separate interpreter, separate everything, torn down via
`loopwall.proctree.kill_tree` (the same mechanism `RingCapture` itself relies on) rather
than any in-process signal.

Deliberately not `loopwall play` itself: that command's no-`SCENARIO` mode also opens a
real OS browser tab (`webbrowser.open`), which is exactly what a Playwright-driven test
doesn't want fighting it for control of the page.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import uvicorn

from loopwall.capture.extract import extract_window
from loopwall.capture.ring import RingCapture
from loopwall.core import Core
from loopwall.core.scheduler import Scheduler
from loopwall.core.session import SessionArtifact
from loopwall.core.timeline import Timeline
from loopwall.core.tracks import TrackRegistry
from loopwall.drivers.cli.wall import CameraSettings, WallDriver
from loopwall.midi import MidiInput
from loopwall.midi import is_available as midi_is_available
from loopwall.monitor.camera import build_input_args, detect_default_camera
from loopwall.server.app import create_app
from loopwall.server.relay import PreviewRelay


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ring-dir", type=Path, required=True)
    parser.add_argument("--session-root", type=Path, required=True)
    parser.add_argument("--preview-port", type=int, required=True)
    parser.add_argument("--wall-port", type=int, required=True)
    parser.add_argument(
        "--extract-delay-seconds",
        type=float,
        default=0.0,
        help=(
            "Artificially slow extract_window down - stands in for the "
            "~3.5s+ this session measured against real 1280x720 hardware, "
            "which lavfi's tiny synthetic frames finish too fast to exercise "
            "the extracting-tile UI deterministically otherwise."
        ),
    )
    parser.add_argument(
        "--real-hardware",
        action="store_true",
        help=(
            "Use a real, auto-detected camera (detect_default_camera - "
            "dshow/uvc) and attach a real MidiInput (auto-attempts the "
            "first available port) instead of the default synthetic lavfi "
            "source and no MIDI. For hardware-marked browser tests only - "
            "see test_wall_hardware.py."
        ),
    )
    args = parser.parse_args()

    extract_fn = extract_window
    if args.extract_delay_seconds > 0:

        def extract_fn(ring, t_a, t_b, out_path):
            time.sleep(args.extract_delay_seconds)
            return extract_window(ring, t_a, t_b, out_path)

    if args.real_hardware:
        camera_type, device = detect_default_camera()
        width, height, fps = 1280, 720, 30
    else:
        camera_type, device = "lavfi", None
        width, height, fps = 320, 240, 10

    input_args = build_input_args(camera_type, device, width, height, fps, None)
    ring = RingCapture(
        ring_dir=args.ring_dir,
        input_args=input_args,
        segment_seconds=1.0,
        ring_seconds=30.0,
        fps=fps,
        max_runtime_seconds=90.0,  # hard ceiling regardless of test outcome
        preview_tcp_port=args.preview_port,
    )
    session = SessionArtifact(args.session_root, bpm=0.0, beats_per_bar=1)
    core = Core(
        timeline=Timeline(),
        tracks=TrackRegistry(),
        scheduler=Scheduler(),
        session=session,
        ring=ring,
        default_camera_id="cam0",
        extract_fn=extract_fn,
    )
    camera = CameraSettings(
        camera_type=camera_type,
        device=device,
        width=width,
        height=height,
        fps=fps,
        ring_seconds=30.0,
        segment_seconds=1.0,
        ring_dir_base=args.ring_dir,
    )
    midi_input = MidiInput() if args.real_hardware and midi_is_available() else None
    driver = WallDriver(
        core, ring, PreviewRelay(), session_root=args.session_root, camera=camera, midi=midi_input
    )
    app = create_app(driver)

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.wall_port,
        log_level="warning",
        timeout_graceful_shutdown=3,  # see __main__.py's own note on the infinite preview stream
    )


if __name__ == "__main__":
    main()
