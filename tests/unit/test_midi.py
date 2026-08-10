"""Unit tests for loopwall.midi - pure logic only, no mido/real hardware needed.

MidiInput's own message-handling is exercised via handle_message() with
plain fake message objects (mido message objects are already duck-typed
this way) - deliberately not through a real mido port, since python-rtmidi
can't even be installed on every dev machine (see midi.py's own docstring).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from loopwall.midi import ClockTracker, MidiInput, MidiTrigger


@dataclass
class FakeMsg:
    type: str
    channel: int = 0
    note: int | None = None
    control: int | None = None
    velocity: int = 64


# --- ClockTracker -----------------------------------------------------------


def test_bar_completes_after_beats_per_bar_times_24_pulses() -> None:
    tracker = ClockTracker(beats_per_bar=4)
    tracker.start()
    results = [tracker.pulse(now=i * 0.02) for i in range(1, 96)]
    assert results == [False] * 95
    assert tracker.pulse(now=96 * 0.02) is True


def test_one_beat_bar_completes_every_24_pulses() -> None:
    tracker = ClockTracker(beats_per_bar=1)
    tracker.start()
    assert [tracker.pulse(now=i * 0.02) for i in range(1, 24)] == [False] * 23
    assert tracker.pulse(now=24 * 0.02) is True
    # and again, immediately, for the next bar
    assert [tracker.pulse(now=(24 + i) * 0.02) for i in range(1, 24)] == [False] * 23
    assert tracker.pulse(now=48 * 0.02) is True


def test_pulses_ignored_before_start() -> None:
    tracker = ClockTracker(beats_per_bar=1)
    for i in range(1, 30):
        assert tracker.pulse(now=i * 0.02) is False
    assert tracker.last_pulse_at is None


def test_stop_then_pulses_ignored_until_start_again() -> None:
    tracker = ClockTracker(beats_per_bar=1)
    tracker.start()
    tracker.pulse(now=0.02)
    tracker.stop()
    assert tracker.pulse(now=0.04) is False
    tracker.start()
    assert [tracker.pulse(now=(2 + i) * 0.02) for i in range(1, 24)] == [False] * 23
    assert tracker.pulse(now=26 * 0.02) is True


def test_bpm_computed_from_pulse_interval() -> None:
    # 120bpm, 4/4 -> 0.5s/beat -> 0.5/24 s/pulse
    tracker = ClockTracker(beats_per_bar=4)
    tracker.start()
    interval = 0.5 / 24
    now = 0.0
    for _ in range(30):
        now += interval
        tracker.pulse(now)
    assert tracker.bpm == pytest.approx(120.0, abs=0.5)


def test_huge_gap_does_not_corrupt_bpm_average() -> None:
    tracker = ClockTracker(beats_per_bar=4)
    tracker.start()
    interval = 0.5 / 24
    now = 0.0
    for _ in range(10):
        now += interval
        tracker.pulse(now)
    steady_bpm = tracker.bpm
    now += 5.0  # a large gap - e.g. resuming after being stopped externally
    tracker.pulse(now)
    assert tracker.bpm == pytest.approx(steady_bpm, abs=0.5)


def test_is_active_respects_timeout() -> None:
    tracker = ClockTracker()
    tracker.start()
    tracker.pulse(now=10.0)
    assert tracker.is_active(now=11.0, timeout=2.0) is True
    assert tracker.is_active(now=13.0, timeout=2.0) is False


def test_is_active_false_when_never_started() -> None:
    tracker = ClockTracker()
    assert tracker.is_active(now=0.0) is False


def test_is_active_false_after_stop() -> None:
    tracker = ClockTracker()
    tracker.start()
    tracker.pulse(now=1.0)
    tracker.stop()
    assert tracker.is_active(now=1.1) is False


# --- MidiTrigger --------------------------------------------------------------


def test_note_trigger_matches_exact_note_channel() -> None:
    trigger = MidiTrigger(kind="note_on", channel=2, number=36)
    assert trigger.matches(FakeMsg(type="note_on", channel=2, note=36)) is True
    assert trigger.matches(FakeMsg(type="note_on", channel=2, note=37)) is False
    assert trigger.matches(FakeMsg(type="note_on", channel=3, note=36)) is False
    assert trigger.matches(FakeMsg(type="control_change", channel=2, control=36)) is False


def test_cc_trigger_matches_exact_control_channel() -> None:
    trigger = MidiTrigger(kind="control_change", channel=0, number=64)
    assert trigger.matches(FakeMsg(type="control_change", channel=0, control=64)) is True
    assert trigger.matches(FakeMsg(type="control_change", channel=0, control=65)) is False


# --- MidiInput.handle_message (no real port/mido needed) ---------------------


def _driver() -> tuple[MidiInput, list[str]]:
    calls: list[str] = []
    midi = MidiInput(beats_per_bar=1)  # 1 beat/bar -> a bar completes every 24 pulses
    midi._on_bar = lambda: calls.append("bar")
    midi._on_trigger = lambda: calls.append("trigger")
    midi._on_camera_switch = lambda camera_type, device: calls.append(f"camera:{camera_type}:{device}")
    midi._schedule = lambda fn: fn()  # synchronous for the test - no real event loop needed
    return midi, calls


def test_clock_messages_fire_on_bar_at_the_boundary() -> None:
    midi, calls = _driver()
    midi.handle_message(FakeMsg(type="start"))
    for _ in range(23):
        midi.handle_message(FakeMsg(type="clock"))
    assert calls == []
    midi.handle_message(FakeMsg(type="clock"))
    assert calls == ["bar"]


def test_clock_before_start_message_never_fires_bar() -> None:
    midi, calls = _driver()
    for _ in range(50):
        midi.handle_message(FakeMsg(type="clock"))
    assert calls == []


def test_learn_mode_captures_next_note_and_stops_learning() -> None:
    midi, calls = _driver()
    midi.start_learn()
    assert midi.learning is True
    midi.handle_message(FakeMsg(type="note_on", channel=1, note=40, velocity=100))
    assert midi.learning is False
    assert midi.trigger == MidiTrigger(kind="note_on", channel=1, number=40)
    assert calls == []  # the learn message itself doesn't also fire a toggle


def test_learn_mode_captures_cc() -> None:
    midi, _ = _driver()
    midi.start_learn()
    midi.handle_message(FakeMsg(type="control_change", channel=0, control=20, velocity=127))
    assert midi.trigger == MidiTrigger(kind="control_change", channel=0, number=20)


def test_matching_trigger_after_learn_fires_on_trigger() -> None:
    midi, calls = _driver()
    midi.start_learn()
    midi.handle_message(FakeMsg(type="note_on", channel=0, note=36, velocity=100))
    midi.handle_message(FakeMsg(type="note_on", channel=0, note=36, velocity=100))
    assert calls == ["trigger"]


def test_non_matching_note_does_not_fire_trigger() -> None:
    midi, calls = _driver()
    midi.start_learn()
    midi.handle_message(FakeMsg(type="note_on", channel=0, note=36, velocity=100))
    midi.handle_message(FakeMsg(type="note_on", channel=0, note=37, velocity=100))
    assert calls == []


def test_note_on_velocity_zero_treated_as_note_off_not_a_trigger() -> None:
    midi, calls = _driver()
    midi.start_learn()
    midi.handle_message(FakeMsg(type="note_on", channel=0, note=36, velocity=100))
    midi.handle_message(FakeMsg(type="note_on", channel=0, note=36, velocity=0))
    assert calls == []


def test_no_trigger_set_yet_never_fires() -> None:
    midi, calls = _driver()
    midi.handle_message(FakeMsg(type="note_on", channel=0, note=36, velocity=100))
    assert calls == []


# --- MidiInput.camera_triggers -------------------------------------------------


def test_bound_camera_trigger_fires_camera_switch_not_toggle() -> None:
    midi, calls = _driver()
    trigger = MidiTrigger(kind="note_on", channel=0, number=36)
    midi.bind_camera_trigger(trigger, "lavfi", "sim0")
    midi.handle_message(FakeMsg(type="note_on", channel=0, note=36, velocity=100))
    assert calls == ["camera:lavfi:sim0"]


def test_multiple_camera_triggers_route_to_the_right_camera() -> None:
    midi, calls = _driver()
    midi.bind_camera_trigger(MidiTrigger(kind="note_on", channel=0, number=36), "lavfi", "sim0")
    midi.bind_camera_trigger(MidiTrigger(kind="control_change", channel=1, number=20), "dshow", "Cam1")
    midi.handle_message(FakeMsg(type="control_change", channel=1, control=20, velocity=127))
    midi.handle_message(FakeMsg(type="note_on", channel=0, note=36, velocity=100))
    assert calls == ["camera:dshow:Cam1", "camera:lavfi:sim0"]


def test_binding_a_trigger_to_a_camera_unbinds_it_from_the_toggle() -> None:
    midi, calls = _driver()
    trigger = MidiTrigger(kind="note_on", channel=0, number=36)
    midi.trigger = trigger
    midi.bind_camera_trigger(trigger, "lavfi", "sim0")
    assert midi.trigger is None
    midi.handle_message(FakeMsg(type="note_on", channel=0, note=36, velocity=100))
    assert calls == ["camera:lavfi:sim0"]


def test_rebinding_a_camera_replaces_its_previous_trigger() -> None:
    midi, calls = _driver()
    first = MidiTrigger(kind="note_on", channel=0, number=36)
    second = MidiTrigger(kind="control_change", channel=0, number=20)
    midi.bind_camera_trigger(first, "lavfi", "sim0")
    midi.bind_camera_trigger(second, "lavfi", "sim0")
    assert midi.camera_triggers == [(second, "lavfi", "sim0")]
    midi.handle_message(FakeMsg(type="note_on", channel=0, note=36, velocity=100))
    assert calls == []  # the old binding no longer fires anything
    midi.handle_message(FakeMsg(type="control_change", channel=0, control=20, velocity=127))
    assert calls == ["camera:lavfi:sim0"]


def test_rebinding_a_trigger_to_a_different_camera_replaces_the_old_binding() -> None:
    midi, calls = _driver()
    trigger = MidiTrigger(kind="note_on", channel=0, number=36)
    midi.bind_camera_trigger(trigger, "lavfi", "sim0")
    midi.bind_camera_trigger(trigger, "dshow", "Cam1")
    assert midi.camera_triggers == [(trigger, "dshow", "Cam1")]
    midi.handle_message(FakeMsg(type="note_on", channel=0, note=36, velocity=100))
    assert calls == ["camera:dshow:Cam1"]


# --- MidiInput.history --------------------------------------------------------


def test_history_records_note_and_cc_activity_even_outside_learn_mode() -> None:
    midi, _ = _driver()
    midi.handle_message(FakeMsg(type="note_on", channel=0, note=36, velocity=100))
    midi.handle_message(FakeMsg(type="control_change", channel=1, control=20, velocity=127))
    assert list(midi.history) == [
        MidiTrigger(kind="note_on", channel=0, number=36),
        MidiTrigger(kind="control_change", channel=1, number=20),
    ]


def test_history_dedupes_a_held_note_repeated_immediately() -> None:
    midi, _ = _driver()
    midi.handle_message(FakeMsg(type="note_on", channel=0, note=36, velocity=100))
    midi.handle_message(FakeMsg(type="note_on", channel=0, note=36, velocity=100))
    assert list(midi.history) == [MidiTrigger(kind="note_on", channel=0, number=36)]


def test_history_is_bounded() -> None:
    midi, _ = _driver()
    for note in range(midi.HISTORY_SIZE + 5):
        midi.handle_message(FakeMsg(type="note_on", channel=0, note=note, velocity=100))
    assert len(midi.history) == midi.HISTORY_SIZE
    # oldest entries fall off the front - the most recent notes are what's kept
    assert midi.history[-1] == MidiTrigger(kind="note_on", channel=0, number=midi.HISTORY_SIZE + 4)


def test_learning_still_records_the_learned_message_into_history() -> None:
    midi, _ = _driver()
    midi.start_learn()
    midi.handle_message(FakeMsg(type="note_on", channel=0, note=36, velocity=100))
    assert list(midi.history) == [MidiTrigger(kind="note_on", channel=0, number=36)]


# --- MidiInput.bindings_to_dict / apply_bindings_dict -------------------------


def test_bindings_round_trip_with_trigger_and_camera_triggers() -> None:
    midi, _ = _driver()
    midi.trigger = MidiTrigger(kind="note_on", channel=0, number=36)
    midi.bind_camera_trigger(MidiTrigger(kind="control_change", channel=1, number=20), "dshow", "Cam1")
    data = midi.bindings_to_dict()

    restored, _ = _driver()
    restored.apply_bindings_dict(data)
    assert restored.trigger == midi.trigger
    assert restored.camera_triggers == midi.camera_triggers


def test_bindings_round_trip_with_no_toggle_trigger_set() -> None:
    midi, _ = _driver()
    midi.bind_camera_trigger(MidiTrigger(kind="note_on", channel=0, number=36), "lavfi", "sim0")
    assert midi.trigger is None  # binding a camera trigger never sets the toggle
    data = midi.bindings_to_dict()
    assert data["trigger"] is None

    restored, _ = _driver()
    restored.trigger = MidiTrigger(kind="note_on", channel=2, number=1)  # something to overwrite
    restored.apply_bindings_dict(data)
    assert restored.trigger is None
    assert restored.camera_triggers == midi.camera_triggers


def test_bindings_to_dict_is_plain_json_able_data() -> None:
    midi, _ = _driver()
    midi.trigger = MidiTrigger(kind="note_on", channel=0, number=36)
    midi.bind_camera_trigger(MidiTrigger(kind="control_change", channel=1, number=20), "dshow", "Cam1")
    data = midi.bindings_to_dict()
    assert data == {
        "trigger": {"kind": "note_on", "channel": 0, "number": 36},
        "camera_triggers": [
            {"kind": "control_change", "channel": 1, "number": 20, "camera_type": "dshow", "device": "Cam1"}
        ],
    }


def test_apply_bindings_dict_defaults_missing_camera_triggers_to_empty() -> None:
    midi, _ = _driver()
    midi.apply_bindings_dict({"trigger": None})
    assert midi.trigger is None
    assert midi.camera_triggers == []


def test_on_message_contains_a_bad_callback_instead_of_killing_the_thread(
    capsys: pytest.CaptureFixture,
) -> None:
    """A raising on_bar/on_trigger must not take down rtmidi's callback thread - the
    next real message still needs to be handled. _on_message is the actual mido
    callback (handle_message is the pure logic underneath it, tested everywhere else
    in this file); this exercises the try/except wrapper around it directly."""
    midi, calls = _driver()

    def _boom() -> None:
        raise ValueError("boom")

    midi._on_bar = _boom
    midi.handle_message(FakeMsg(type="start"))
    for _ in range(23):
        midi._on_message(FakeMsg(type="clock"))
    midi._on_message(FakeMsg(type="clock"))  # completes the bar -> on_bar fires -> raises
    assert "MIDI callback error" in capsys.readouterr().err

    midi._on_bar = lambda: calls.append("bar")
    midi.handle_message(FakeMsg(type="start"))
    for _ in range(23):
        midi._on_message(FakeMsg(type="clock"))
    midi._on_message(FakeMsg(type="clock"))
    assert calls == ["bar"]
