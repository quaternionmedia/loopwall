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
from loopwall.drivers.cli.wall import CameraSettings, WallDriver
from loopwall.midi import MidiInput, MidiTrigger

pytestmark = pytest.mark.anyio


class FakeRing:
    def __init__(self) -> None:
        self.t0 = 1000.0
        self.preview_tcp_port = 12345

    def segments(self) -> list[Segment]:
        # `inf`, not a large-but-finite sentinel: Timeline.bar_to_time stamps
        # against real time.monotonic() (process/system uptime), so a finite
        # value only "always covers" until uptime exceeds it (see test_core.py's
        # FakeRing for the same fix and the full rationale).
        return [Segment(Path("fake.mp4"), 0.0, float("inf"))]

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

    session_root = tmp_path / "sessions"
    session = SessionArtifact(session_root, bpm=0.0, beats_per_bar=1)
    core = Core(
        timeline=Timeline(),
        tracks=TrackRegistry(),
        scheduler=Scheduler(),
        session=session,
        ring=FakeRing(),
        extract_fn=fake_extract,
    )
    camera = CameraSettings(
        camera_type="lavfi",
        device=None,
        width=320,
        height=240,
        fps=10,
        ring_seconds=120.0,
        segment_seconds=2.0,
        ring_dir_base=tmp_path / "ring",
    )
    return WallDriver(core, FakeRing(), FakeRelay(), session_root=session_root, camera=camera)


async def _prime(driver: WallDriver) -> None:
    driver._t_prev = time.monotonic()
    await driver._tick(playing=False)


async def _advance(driver: WallDriver) -> None:
    """Apply whatever's currently armed (if anything) plus one bar tick -
    exactly what the internal ticker or a MIDI bar boundary does.

    A tiny real sleep first: back-to-back ticks with no awaited work between
    them can land within the same clock-resolution window and compute an
    identical bpm (`elapsed = now - self._t_prev` rounds to the same value
    both times) - `Timeline.on_transport()` dedupes same-bpm breakpoints, so
    two calls to this helper in immediate succession can collapse into one
    breakpoint and make a capture's `t_a`/`t_b` resolve identically. Never
    realistic in production (the real ticker sleeps a real 0.2s between
    ticks), but a test calling this in a tight loop can hit it.
    """
    await asyncio.sleep(0.001)
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


async def test_new_session_creates_a_fresh_session_and_clears_tiles(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.toggle()
    await _advance(driver)
    await driver.toggle()
    await _advance(driver)
    old_session_id = driver.core.session.session_id
    assert len(driver.state()["tiles"]) == 1

    await driver.new_session()

    assert driver.state()["tiles"] == []
    assert driver.core.session.session_id != old_session_id

    # the next take starts back at track 1, not continuing the old numbering
    await driver.toggle()
    await _advance(driver)
    await driver.toggle()
    await _advance(driver)
    assert driver.state()["tiles"][0]["track"] == 1


async def test_new_session_is_blocked_while_recording(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.toggle()
    await _advance(driver)  # now actually recording
    old_session_id = driver.core.session.session_id

    with pytest.raises(RuntimeError, match="recording"):
        await driver.new_session()

    assert driver.core.session.session_id == old_session_id
    assert driver.state()["recording"] is True


async def test_set_label_sets_and_clears(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.toggle()
    await _advance(driver)
    await driver.toggle()
    await _advance(driver)
    track = driver.state()["tiles"][0]["track"]

    await driver.set_label(track, "intro loop")
    assert driver.state()["tiles"][0]["label"] == "intro loop"

    await driver.set_label(track, "")  # empty text clears, not an empty-string label
    assert driver.state()["tiles"][0]["label"] is None


async def test_set_label_unknown_track_is_a_no_op(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.set_label(99, "whatever")
    assert driver.state()["tiles"] == []


async def test_move_tile_swaps_neighbors_and_clamps_at_the_ends(driver: WallDriver) -> None:
    await _prime(driver)
    for _ in range(2):
        await driver.toggle()
        await _advance(driver)
        await driver.toggle()
        await _advance(driver)
    track1, track2 = (t["track"] for t in driver.state()["tiles"])

    await driver.move_tile(track2, "left")
    assert [t["track"] for t in driver.state()["tiles"]] == [track2, track1]

    await driver.move_tile(track2, "left")  # already leftmost - no-op
    assert [t["track"] for t in driver.state()["tiles"]] == [track2, track1]

    await driver.move_tile(track1, "right")  # already rightmost - no-op
    assert [t["track"] for t in driver.state()["tiles"]] == [track2, track1]


async def test_move_tile_unknown_track_is_a_no_op(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.move_tile(99, "left")
    assert driver.state()["tiles"] == []


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


async def test_import_clip_adds_a_playing_tile(driver: WallDriver, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("loopwall.drivers.cli.wall.probe_duration", lambda path: 3.5)
    await _prime(driver)

    await driver.import_clip("my-clip.mp4", b"fake video bytes")

    state = driver.state()
    assert len(state["tiles"]) == 1
    tile = state["tiles"][0]
    assert tile["track"] == 1
    assert tile["state"] == "playing"
    assert tile["duration_seconds"] == 3.5
    assert tile["clip_url"] == f"/clips/{driver.core.session.session_id}/track01_p1_import.mp4"


async def test_import_clip_refuses_while_recording(
    driver: WallDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("loopwall.drivers.cli.wall.probe_duration", lambda path: 3.5)
    await _prime(driver)
    await driver.toggle()
    await _advance(driver)  # now actually recording

    with pytest.raises(RuntimeError, match="recording"):
        await driver.import_clip("my-clip.mp4", b"fake video bytes")

    assert driver.state()["tiles"] == []


async def test_import_clip_bad_file_cleans_up_and_raises(
    driver: WallDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(path: Path) -> float:
        raise ValueError("not a video")

    monkeypatch.setattr("loopwall.drivers.cli.wall.probe_duration", _boom)
    await _prime(driver)

    with pytest.raises(RuntimeError, match="couldn't read"):
        await driver.import_clip("bad.mp4", b"not really a video")

    assert driver.state()["tiles"] == []
    assert list(driver.core.session.dir.glob("*import*")) == []

    # the track number is given back, not burned - the next real take still starts at 1
    await driver.toggle()
    await _advance(driver)
    await driver.toggle()
    await _advance(driver)
    assert driver.state()["tiles"][0]["track"] == 1


async def test_load_session_resumes_tiles_and_continues_track_numbering(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.toggle()
    await _advance(driver)
    await driver.toggle()
    await _advance(driver)
    old_session_id = driver.core.session.session_id
    assert len(driver.state()["tiles"]) == 1

    await driver.new_session()
    assert driver.state()["tiles"] == []

    await driver.load_session(old_session_id)

    state = driver.state()
    assert state["session"]["id"] == old_session_id
    assert len(state["tiles"]) == 1
    assert state["tiles"][0]["track"] == 1

    # the next take continues numbering from the resumed session, not restarting at 1
    await driver.toggle()
    await _advance(driver)
    await driver.toggle()
    await _advance(driver)
    assert [t["track"] for t in driver.state()["tiles"]] == [1, 2]


async def test_load_session_reseeds_track_registry_so_clear_fires_a_real_remove(
    driver: WallDriver,
) -> None:
    """The specific bug this reseeding exists to prevent: a fresh Core's TrackRegistry
    defaults an unseen track to "empty" (core/tracks.py), so without re-dispatching
    TrackEvent(state="playing") for a resumed tile, clear()'s "empty"->"empty"
    transition would fire no committed op at all.
    """
    await _prime(driver)
    await driver.toggle()
    await _advance(driver)
    await driver.toggle()
    await _advance(driver)
    session_id = driver.core.session.session_id

    await driver.new_session()
    await driver.load_session(session_id)
    committed_before = len(driver.core.scheduler.committed)

    await driver.clear(1)

    assert driver.state()["tiles"] == []
    assert len(driver.core.scheduler.committed) == committed_before + 1
    assert driver.core.scheduler.committed[-1].kind == "remove"


async def test_load_session_is_blocked_while_recording(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.toggle()
    await _advance(driver)  # now actually recording
    with pytest.raises(RuntimeError, match="recording"):
        await driver.load_session("whatever")


async def test_load_session_unknown_session_id_raises_value_error(driver: WallDriver) -> None:
    await _prime(driver)
    with pytest.raises(ValueError, match="no session"):
        await driver.load_session("does-not-exist")


async def test_switch_camera_refuses_while_recording(driver: WallDriver) -> None:
    await _prime(driver)
    await driver.toggle()
    await _advance(driver)  # now actually recording - guard fires before touching the ring
    with pytest.raises(RuntimeError, match="recording"):
        await driver.switch_camera("lavfi", None)


async def test_select_midi_trigger_sets_trigger_directly(driver: WallDriver) -> None:
    driver.midi = MidiInput(beats_per_bar=1)
    driver.select_midi_trigger(kind="note_on", channel=2, number=40)
    assert driver.midi.trigger == MidiTrigger(kind="note_on", channel=2, number=40)


async def test_select_midi_trigger_without_midi_is_a_no_op(driver: WallDriver) -> None:
    driver.select_midi_trigger(kind="note_on", channel=0, number=1)  # no midi attached; shouldn't raise


async def test_export_config_matches_config_example_shape(driver: WallDriver) -> None:
    config = driver.export_config()
    assert config["driver"]["type"] == "cli"
    assert config["capture"]["cameras"][0]["type"] == "lavfi"
    assert config["capture"]["cameras"][0]["res"] == "320x240"
    assert config["capture"]["ring"]["seconds"] == 120.0
    assert "session_root" in config
    assert config["server"]["headless"] is True


async def test_start_render_refuses_when_grid_is_empty(driver: WallDriver) -> None:
    await _prime(driver)
    with pytest.raises(RuntimeError, match="empty"):
        await driver.start_render()


@dataclass
class _FakeMsg:
    type: str
    channel: int = 0
    note: int | None = None
    control: int | None = None
    velocity: int = 100
