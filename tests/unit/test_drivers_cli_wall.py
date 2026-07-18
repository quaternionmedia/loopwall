"""Unit tests for WallDriver's arm/apply, clear/reset, and MIDI-wiring logic - no real ffmpeg involved.

Mirrors test_core.py's FakeRing pattern: Core's own machinery is real,
only the ring/relay are faked. `_prime()` mimics the event-dispatch part
of WallDriver.start() (hello + bar-0 tick) without touching real
ring/relay process startup, since those need real sockets/subprocesses.
`_advance()` applies one pending action (if any) plus a bar tick - the
same thing the internal ticker or a MIDI bar boundary would do; `toggle()`
itself only ever arms, per WallDriver's own docstring.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from loopwall.capture.ring import Segment
from loopwall.core import Core
from loopwall.core.scheduler import Scheduler
from loopwall.core.session import SessionArtifact
from loopwall.core.timeline import Timeline
from loopwall.core.tracks import TrackRegistry
from loopwall.drivers.cli.wall import WallDriver
from loopwall.midi import MidiInput, MidiTrigger

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


async def _advance(driver: WallDriver) -> None:
    """Apply whatever's currently armed (if anything) plus one bar tick -
    exactly what the internal ticker or a MIDI bar boundary does."""
    await driver._on_midi_bar()


async def test_toggle_only_arms_does_not_start_immediately(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.toggle()

    state = driver.state()
    assert state["armed"] == "start"
    assert state["recording"] is False
    assert state["tiles"] == []


async def test_next_tick_applies_the_armed_start(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.toggle()
    await _advance(driver)

    state = driver.state()
    assert state["armed"] is None
    assert state["recording"] is True
    assert state["record_seconds"] is not None


async def test_full_start_stop_cycle_creates_exactly_one_playing_tile(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.toggle()  # arm start
    await _advance(driver)  # apply start
    await driver.toggle()  # arm stop
    await _advance(driver)  # apply stop

    state = driver.state()
    assert state["recording"] is False
    assert state["armed"] is None
    assert len(state["tiles"]) == 1
    tile = state["tiles"][0]
    assert tile["track"] == 1
    assert tile["state"] == "playing"
    assert tile["clip_url"].startswith("/clips/")
    assert tile["duration_seconds"] == 2.0  # from the monkeypatched probe_duration


async def test_repeated_toggle_while_armed_is_a_no_op(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.toggle()
    await driver.toggle()  # repeat press before the arm applies - ignored

    assert driver.state()["armed"] == "start"


async def test_second_take_becomes_a_second_track_not_a_replacement(driver: WallDriver) -> None:
    await _prime(driver)
    for _ in range(2):
        await driver.toggle()
        await _advance(driver)
        await driver.toggle()
        await _advance(driver)

    state = driver.state()
    assert [t["track"] for t in state["tiles"]] == [1, 2]
    assert len({t["clip_url"] for t in state["tiles"]}) == 2  # distinct clips, not the same one twice


async def test_toggle_stop_before_min_record_seconds_is_ignored(
    driver: WallDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("loopwall.drivers.cli.wall.MIN_RECORD_SECONDS", 1000.0)
    await _prime(driver)
    await driver.toggle()
    await _advance(driver)  # apply start
    await driver.toggle()  # stop attempt - too soon, never arms

    state = driver.state()
    assert state["armed"] is None
    assert state["recording"] is True  # still recording - the stop never took effect
    assert state["tiles"] == []


async def test_clear_removes_one_tile(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.toggle()
    await _advance(driver)
    await driver.toggle()
    await _advance(driver)
    assert len(driver.state()["tiles"]) == 1

    await driver.clear(1)

    assert driver.state()["tiles"] == []


async def test_clear_unknown_track_is_a_no_op(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.clear(99)
    assert driver.state()["tiles"] == []


async def test_reset_clears_every_tile(driver: WallDriver) -> None:
    await _prime(driver)
    for _ in range(2):
        await driver.toggle()
        await _advance(driver)
        await driver.toggle()
        await _advance(driver)
    assert len(driver.state()["tiles"]) == 2

    await driver.reset()

    assert driver.state()["tiles"] == []


async def test_reset_cancels_a_pending_arm(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.toggle()
    assert driver.state()["armed"] == "start"

    await driver.reset()

    assert driver.state()["armed"] is None
    await _advance(driver)
    assert driver.state()["recording"] is False  # the cancelled arm never applied


async def test_reset_does_not_touch_an_active_recording(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.toggle()
    await _advance(driver)  # now actually recording
    assert driver.state()["recording"] is True

    await driver.reset()

    assert driver.state()["recording"] is True


async def test_state_has_no_midi_block_without_a_midi_input(driver: WallDriver) -> None:
    assert driver.state()["midi"] is None


async def test_midi_trigger_arms_a_toggle_via_the_shared_toggle_path(driver: WallDriver) -> None:
    midi = MidiInput(beats_per_bar=1)
    driver.midi = midi
    await _prime(driver)

    # Wired the same way start() does it (on_trigger schedules a task, it
    # doesn't await toggle() directly - a bare `lambda: driver.toggle()`
    # would only ever create the coroutine, never run it), just with a
    # synchronous `_schedule` standing in for call_soon_threadsafe since
    # this test has no real MIDI callback thread to cross from.
    midi._on_bar = lambda: None
    midi._on_trigger = lambda: asyncio.create_task(driver.toggle())
    midi._schedule = lambda fn: fn()
    midi.trigger = MidiTrigger(kind="note_on", channel=0, number=36)

    midi.handle_message(_FakeMsg(type="note_on", channel=0, note=36))
    await asyncio.sleep(0)  # let the scheduled toggle() task actually run

    assert driver.state()["armed"] == "start"


@dataclass
class _FakeMsg:
    type: str
    channel: int = 0
    note: int | None = None
    control: int | None = None
    velocity: int = 100
