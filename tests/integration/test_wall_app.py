"""End-to-end wall app lifecycle: real Core, real lavfi ring, real
PreviewRelay (a plain asyncio TCP server, no ffmpeg - see server/relay.py)
- via FastAPI's TestClient, which drives the app's real lifespan
(WallDriver.start()/stop()) without needing a live server socket.

Not `hardware`-marked - lavfi never touches real capture hardware, so
this is safe to run anywhere ffmpeg is installed; skipped, not failed,
when it isn't (matching test_monitor_lifecycle.py's convention).
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from loopwall.capture.ring import RingCapture
from loopwall.core import Core
from loopwall.core.scheduler import Scheduler
from loopwall.core.session import SessionArtifact
from loopwall.core.timeline import Timeline
from loopwall.core.tracks import TrackRegistry
from loopwall.drivers.cli.wall import WallDriver
from loopwall.monitor.camera import build_input_args
from loopwall.server.app import create_app
from loopwall.server.relay import PreviewRelay

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _build(tmp_path: Path, preview_port: int) -> WallDriver:
    input_args = build_input_args("lavfi", None, 320, 240, 10, None)
    ring = RingCapture(
        ring_dir=tmp_path / "ring",
        input_args=input_args,
        segment_seconds=1.0,
        ring_seconds=20.0,
        fps=10,
        max_runtime_seconds=25.0,  # hard ceiling regardless of test outcome
        preview_tcp_port=preview_port,
    )
    session = SessionArtifact(tmp_path / "sessions", bpm=0.0, beats_per_bar=1)
    core = Core(
        timeline=Timeline(),
        tracks=TrackRegistry(),
        scheduler=Scheduler(),
        session=session,
        ring=ring,
        default_camera_id="cam0",
    )
    return WallDriver(core, ring, PreviewRelay())


def _poll_until(client: TestClient, predicate, timeout: float = 5.0) -> dict:
    """Poll /wall/state until predicate(state) is true - the internal ticker
    (0.2s cadence) applies an armed action asynchronously, on its own
    schedule, not synchronously within the /wall/control response."""
    deadline = time.monotonic() + timeout
    state = client.get("/wall/state").json()
    while not predicate(state):
        if time.monotonic() > deadline:
            raise TimeoutError(f"condition never became true; last state was {state}")
        time.sleep(0.1)
        state = client.get("/wall/state").json()
    return state


def test_wall_page_state_control_and_clip_serving(tmp_path: Path) -> None:
    driver = _build(tmp_path, preview_port=17401)
    app = create_app(driver, clips_dir=driver.core.session.dir, wall_dir=Path("wall"))

    with TestClient(app) as client:
        page = client.get("/wall")
        assert page.status_code == 200
        assert "loopwall" in page.text
        assert "/wall/preview.ts" in page.text

        script = client.get("/wall/mpegts.js")
        assert script.status_code == 200
        assert "mpegts" in script.text

        state = client.get("/wall/state").json()
        assert state["tiles"] == []
        assert state["recording"] is False

        state = client.post("/wall/control", json={"action": "toggle"}).json()
        assert state["armed"] == "start"  # toggle only arms - the internal ticker applies it

        _poll_until(client, lambda s: s["recording"] is True)

        time.sleep(1.1)  # clear WallDriver's MIN_RECORD_SECONDS

        state = client.post("/wall/control", json={"action": "toggle"}).json()
        assert state["armed"] == "stop"

        state = _poll_until(client, lambda s: len(s["tiles"]) == 1)
        assert state["recording"] is False
        clip_url = state["tiles"][0]["clip_url"]

        clip_resp = client.get(clip_url)
        assert clip_resp.status_code == 200
        assert len(clip_resp.content) > 0

        state = client.post("/wall/control", json={"action": "reset"}).json()
        assert state["tiles"] == []
        assert state["midi"] is None  # no --midi requested for this driver

    assert not driver.ring.is_running
