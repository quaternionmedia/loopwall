import pytest
from pydantic import TypeAdapter, ValidationError

from loopwall.ldp.models import (
    AccentEvent,
    Capabilities,
    CaptureCueEvent,
    HelloEvent,
    LdpEvent,
    TrackEvent,
    TransportEvent,
)

_adapter: TypeAdapter[LdpEvent] = TypeAdapter(LdpEvent)


def test_hello_event_defaults() -> None:
    event = HelloEvent(
        driver="cli",
        caps=Capabilities(
            clock="internal", explicit_state=True, capture_cues=True, media_binding=False, style="event"
        ),
    )
    assert event.t == "hello"
    assert event.proto == "ldp/1"


def test_discriminated_union_routes_by_t() -> None:
    parsed = _adapter.validate_python({"t": "track", "track": 1, "state": "recording", "pattern": 2})
    assert isinstance(parsed, TrackEvent)
    assert parsed.pattern == 2


def test_discriminated_union_rejects_unknown_t() -> None:
    with pytest.raises(ValidationError):
        _adapter.validate_python({"t": "nonsense"})


def test_accent_velocity_bounds() -> None:
    AccentEvent(track=1, velocity=0)
    AccentEvent(track=1, velocity=127)
    with pytest.raises(ValidationError):
        AccentEvent(track=1, velocity=128)
    with pytest.raises(ValidationError):
        AccentEvent(track=1, velocity=-1)


def test_transport_beats_per_bar_defaults_to_four() -> None:
    event = TransportEvent(playing=True, bpm=120.0, bar=1, beat=1.0, phase=0.0)
    assert event.beats_per_bar == 4


def test_capture_cue_camera_optional() -> None:
    event = CaptureCueEvent(track=1, pattern=1, cycle_start_bar=2, bars=4)
    assert event.camera is None
