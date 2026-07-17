from pathlib import Path

import pytest
import yaml

from loopwall.drivers.cli import CliDriver, Scenario, TrackDirective


def _load_smoke() -> Scenario:
    raw = yaml.safe_load(Path("scenarios/smoke.yaml").read_text())
    return Scenario.model_validate(raw)


def test_scenario_parses_real_smoke_yaml() -> None:
    scenario = _load_smoke()
    assert scenario.name == "smoke"
    assert scenario.bpm == 120
    assert scenario.beats_per_bar == 4
    assert scenario.script[0].do == "play"
    assert scenario.script[-1].do == "stop"
    record_step = scenario.script[1]
    assert record_step.at_bar == 2
    assert isinstance(record_step.do, TrackDirective)
    assert record_step.do.track == 1
    assert record_step.do.record.bars == 4


def test_track_directive_requires_exactly_one_action() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        TrackDirective(track=1)
    with pytest.raises(ValueError, match="exactly one"):
        TrackDirective(track=1, record={"bars": 2}, state="silent")


def test_beats_per_bar_parses_numerator() -> None:
    scenario = Scenario(name="x", bpm=100, time_signature="3/4", script=[])
    assert scenario.beats_per_bar == 3


class RecordingCore:
    """Stands in for Core - just records the events CliDriver emits, in order."""

    def __init__(self) -> None:
        self.events: list[object] = []

    def handle_event(self, event: object) -> None:
        self.events.append(event)


def test_run_emits_hello_then_transport_before_same_bar_directives() -> None:
    scenario = Scenario.model_validate(
        {
            "name": "fast",
            "bpm": 6000,  # fast enough for a sub-second real-time test
            "time_signature": "4/4",
            "script": [
                {"at_bar": 0, "do": "play"},
                {"at_bar": 1, "do": {"track": 1, "record": {"bars": 2}}},
                {"at_bar": 3, "do": "stop"},
            ],
        }
    )
    core = RecordingCore()
    CliDriver(core, scenario).run()

    kinds = [getattr(e, "t", "hello") for e in core.events]
    assert kinds[0] == "hello"

    # The transport tick for bar 1 must precede that bar's track/capture_cue directives.
    bar1_transport_index = next(
        i for i, e in enumerate(core.events) if getattr(e, "t", None) == "transport" and e.bar == 1
    )
    track_index = next(i for i, e in enumerate(core.events) if getattr(e, "t", None) == "track")
    capture_cue_index = next(i for i, e in enumerate(core.events) if getattr(e, "t", None) == "capture_cue")
    assert bar1_transport_index < track_index < capture_cue_index


def test_record_directive_assigns_ordinal_pattern_per_track() -> None:
    scenario = Scenario.model_validate(
        {
            "name": "two-takes",
            "bpm": 6000,
            "time_signature": "4/4",
            "script": [
                {"at_bar": 0, "do": "play"},
                {"at_bar": 1, "do": {"track": 1, "record": {"bars": 1}}},
                {"at_bar": 2, "do": {"track": 1, "record": {"bars": 1}}},
                {"at_bar": 3, "do": "stop"},
            ],
        }
    )
    core = RecordingCore()
    CliDriver(core, scenario).run()
    patterns = [e.pattern for e in core.events if getattr(e, "t", None) == "track"]
    assert patterns == [1, 2]


def test_reasserting_playing_recalls_last_pattern() -> None:
    scenario = _load_smoke_fast_variant()
    core = RecordingCore()
    CliDriver(core, scenario).run()
    # smoke.yaml's track 1 goes recording(p1) -> silent -> playing; track 2's
    # own recording directive sits in between and must not interfere.
    track1_events = [e for e in core.events if getattr(e, "t", None) == "track" and e.track == 1]
    assert [e.state for e in track1_events] == ["recording", "silent", "playing"]
    assert [e.pattern for e in track1_events] == [1, None, 1]


def _load_smoke_fast_variant() -> Scenario:
    raw = yaml.safe_load(Path("scenarios/smoke.yaml").read_text())
    raw["bpm"] = 6000  # keep the exact shape/bars, just make the test fast
    return Scenario.model_validate(raw)
