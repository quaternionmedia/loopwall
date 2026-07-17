"""Unit tests for WallDriver's toggle/tile logic - no real ffmpeg involved.

Mirrors test_core.py's FakeRing pattern: Core's own machinery is real,
only the ring/relay are faked. `_prime()` mimics the event-dispatch part
of WallDriver.start() (hello + bar-0 tick) without touching real
ring/relay process startup, since those need real sockets/subprocesses.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from loopwall.capture.ring import Segment
from loopwall.core import Core
from loopwall.core.scheduler import Scheduler
from loopwall.core.session import SessionArtifact
from loopwall.core.timeline import Timeline
from loopwall.core.tracks import TrackRegistry
from loopwall.drivers.cli.wall import WallDriver

pytestmark = pytest.mark.anyio


class FakeRing:
    def __init__(self) -> None:
        self.t0 = 1000.0
        self.preview_tcp_port = 12345

    def segments(self) -> list[Segment]:
        return [Segment(Path("fake.mp4"), 0.0, 1_000_000.0)]

    @property
    def is_running(self) -> bool:
        return True

    @property
    def ring_seconds(self) -> float:
        return 120.0

    @property
    def ring_dir(self) -> Path:
        return Path("fake_ring_dir")


class FakeRelay:
    async def start(self, port: int) -> None:
        pass

    async def stop(self) -> None:
        pass


@pytest.fixture
def driver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WallDriver:
    monkeypatch.setattr("loopwall.core.probe_duration", lambda path: 2.0)
    monkeypatch.setattr("loopwall.drivers.cli.wall.MIN_RECORD_SECONDS", 0.0)

    def fake_extract(ring: FakeRing, t_a: float, t_b: float, out_path: Path) -> Path:
        out_path.write_bytes(b"fake")
        return out_path

    session = SessionArtifact(tmp_path, bpm=0.0, beats_per_bar=1)
    core = Core(
        timeline=Timeline(),
        tracks=TrackRegistry(),
        scheduler=Scheduler(),
        session=session,
        ring=FakeRing(),
        extract_fn=fake_extract,
    )
    return WallDriver(core, FakeRing(), FakeRelay())


async def _prime(driver: WallDriver) -> None:
    driver._t_prev = time.monotonic()
    await driver._tick(playing=False)


async def test_start_recording_shows_up_only_as_pending_state(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.toggle()

    state = driver.state()
    assert state["recording"] is True
    assert state["record_seconds"] is not None
    assert state["tiles"] == []  # no tile yet - the pending tile covers "recording" in the UI


async def test_stop_creates_exactly_one_playing_tile(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.toggle()  # start
    await driver.toggle()  # stop

    state = driver.state()
    assert state["recording"] is False
    assert len(state["tiles"]) == 1
    tile = state["tiles"][0]
    assert tile["track"] == 1
    assert tile["state"] == "playing"
    assert tile["clip_url"].startswith("/clips/")


async def test_second_take_becomes_a_second_track_not_a_replacement(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.toggle()
    await driver.toggle()
    await driver.toggle()
    await driver.toggle()

    state = driver.state()
    assert [t["track"] for t in state["tiles"]] == [1, 2]
    assert len({t["clip_url"] for t in state["tiles"]}) == 2  # distinct clips, not the same one twice


async def test_toggle_stop_before_min_record_seconds_is_ignored(
    driver: WallDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("loopwall.drivers.cli.wall.MIN_RECORD_SECONDS", 1000.0)
    await _prime(driver)
    await driver.toggle()  # start
    await driver.toggle()  # stop attempt - too soon, ignored

    state = driver.state()
    assert state["recording"] is True  # still recording - the stop never took effect
    assert state["tiles"] == []
