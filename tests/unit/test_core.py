"""Core dispatch tests - a FakeRing and an injected extract_fn spy stand in for real ffmpeg/ffprobe."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopwall.capture.ring import Segment
from loopwall.core import Core
from loopwall.core.scheduler import Scheduler
from loopwall.core.session import SessionArtifact
from loopwall.core.timeline import Timeline
from loopwall.core.tracks import TrackRegistry
from loopwall.ldp.models import (
    AccentEvent,
    Capabilities,
    CaptureCueEvent,
    ClipBindEvent,
    ConfigHintEvent,
    HelloEvent,
    SceneEvent,
    Seat,
    TrackEvent,
    TransportEvent,
)


class FakeRing:
    """Reports one segment that always covers any requested window - no real ffmpeg needed."""

    def __init__(self) -> None:
        self.t0 = 1000.0

    def segments(self) -> list[Segment]:
        return [Segment(Path("fake.mp4"), 0.0, 1_000_000.0)]


@pytest.fixture
def core_and_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Core, list[tuple[float, float]]]:
    monkeypatch.setattr("loopwall.core.probe_duration", lambda path: 8.0)
    calls: list[tuple[float, float]] = []

    def fake_extract(ring: FakeRing, t_a: float, t_b: float, out_path: Path) -> Path:
        calls.append((t_a, t_b))
        out_path.write_bytes(b"fake")
        return out_path

    session = SessionArtifact(tmp_path, bpm=120.0, beats_per_bar=4)
    core = Core(
        timeline=Timeline(),
        tracks=TrackRegistry(),
        scheduler=Scheduler(),
        session=session,
        ring=FakeRing(),
        extract_fn=fake_extract,
    )
    return core, calls


def _hello() -> HelloEvent:
    return HelloEvent(
        driver="cli",
        caps=Capabilities(
            clock="internal", explicit_state=True, capture_cues=True, media_binding=False, style="event"
        ),
    )


def _transport(bar: int, playing: bool = True) -> TransportEvent:
    return TransportEvent(playing=playing, bpm=120.0, bar=bar, beat=1.0, phase=0.0, beats_per_bar=4)


def test_hello_is_logged_and_otherwise_noop(core_and_calls: tuple[Core, list]) -> None:
    core, _ = core_and_calls
    core.handle_event(_hello())
    logged = list(core.session.log_path.read_text().splitlines())
    assert len(logged) == 1


def test_transport_updates_timeline_and_current_bar(core_and_calls: tuple[Core, list]) -> None:
    core, _ = core_and_calls
    core.handle_event(_transport(0))
    core.handle_event(_transport(1))
    assert core.timeline.bar_to_time(1) == pytest.approx(core.timeline.bar_to_time(0) + 2.0)
    # The current bar is observable indirectly: a committed op's own `bar`
    # comes from Core's internally tracked current bar at commit time.
    core.handle_event(TrackEvent(track=1, state="recording", pattern=1))
    assert core.scheduler.committed[0].bar == 1


def test_track_state_change_commits_op(core_and_calls: tuple[Core, list]) -> None:
    core, _ = core_and_calls
    core.handle_event(_transport(0))
    core.handle_event(TrackEvent(track=1, state="recording", pattern=1))
    assert len(core.scheduler.committed) == 1
    assert core.scheduler.committed[0].kind == "add"


def test_capture_completes_only_at_closing_bar_not_before(core_and_calls: tuple[Core, list]) -> None:
    core, calls = core_and_calls
    core.handle_event(_transport(2))
    core.handle_event(TrackEvent(track=1, state="recording", pattern=1))
    core.handle_event(CaptureCueEvent(track=1, pattern=1, cycle_start_bar=2, bars=4))

    # Bars between cycle_start and the close: no extraction yet.
    for bar in range(3, 6):
        core.handle_event(_transport(bar))
        assert calls == []

    # Bar 6 (2 + 4) closes the window.
    core.handle_event(_transport(6))
    assert len(calls) == 1
    t_a, t_b = calls[0]
    assert t_b - t_a == pytest.approx(8.0)  # 4 bars * 2.0s/bar at 120bpm

    # recording -> playing is presence-preserving: no second committed op.
    assert [op.kind for op in core.scheduler.committed] == ["add"]
    assert core.tracks.get(1).state == "playing"
    assert core.tracks.get(1).pattern == 1


def test_capture_cue_registers_a_clip_in_the_session(core_and_calls: tuple[Core, list]) -> None:
    core, _ = core_and_calls
    core.handle_event(_transport(0))
    core.handle_event(TrackEvent(track=1, state="recording", pattern=1))
    core.handle_event(CaptureCueEvent(track=1, pattern=1, cycle_start_bar=0, bars=2))
    core.handle_event(_transport(1))
    core.handle_event(_transport(2))

    index = json.loads(core.session.index_path.read_text())
    assert len(index["clips"]) == 1
    assert index["clips"][0]["track"] == 1
    assert index["clips"][0]["pattern"] == 1
    assert index["clips"][0]["duration_seconds"] == 8.0  # from the monkeypatched probe_duration


@pytest.mark.parametrize(
    "event",
    [
        AccentEvent(track=1, velocity=100),
        ClipBindEvent(track=1, pattern=1, source="clip.mp4"),
        ConfigHintEvent(names={1: "kick"}),
    ],
)
def test_accent_clip_bind_config_hint_are_logged_and_dropped(
    core_and_calls: tuple[Core, list], event: object
) -> None:
    core, calls = core_and_calls
    core.handle_event(event)  # type: ignore[arg-type]
    logged = list(core.session.log_path.read_text().splitlines())
    assert len(logged) == 1
    assert calls == []


def test_scene_event_raises_via_reconciler_stub(core_and_calls: tuple[Core, list]) -> None:
    core, _ = core_and_calls
    with pytest.raises(NotImplementedError, match="1.1.0"):
        core.handle_event(SceneEvent(seats=[Seat(track=1, source="cam0")]))
