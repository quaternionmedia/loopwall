"""cli driver: REPL + scenario playback against an internal metronome. Reference LDP implementation.

Built at 0.1.0 build order step 5 (ROADMAP.md). See PLAN.md §3.2.

Only scenario playback is built here - the bare interactive REPL is a
natural fast-follow sharing this same machinery (ROADMAP.md defers it
past step 9), not part of this build.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, model_validator

from loopwall.core import Core
from loopwall.ldp.models import Capabilities, CaptureCueEvent, HelloEvent, TrackEvent, TransportEvent


class RecordDirective(BaseModel):
    bars: int


class TrackDirective(BaseModel):
    track: int
    record: RecordDirective | None = None
    state: Literal["empty", "recording", "playing", "silent"] | None = None

    @model_validator(mode="after")
    def _exactly_one_action(self) -> TrackDirective:
        if (self.record is None) == (self.state is None):
            raise ValueError("a track directive needs exactly one of record/state")
        return self


class ScriptStep(BaseModel):
    at_bar: int
    do: Literal["play", "stop"] | TrackDirective


class Scenario(BaseModel):
    """A `scenarios/*.yaml` file's playable shape.

    ``expect`` (assertions a conformance test checks the run against) is
    deliberately not modeled here - it's test-only metadata, not something
    the driver itself consumes, and Pydantic's default extra="ignore"
    lets it pass through a `Scenario.model_validate(raw_yaml_dict)` call
    unmolested.
    """

    name: str
    bpm: float
    time_signature: str = "4/4"
    script: list[ScriptStep]

    @property
    def beats_per_bar(self) -> int:
        return int(self.time_signature.split("/")[0])


class CliDriver:
    """Reference LDP driver: internal metronome ticking a YAML scenario's script into `Core`.

    Ticks a `transport` event for every bar, not just script-directive
    bars - Core's pending-capture check runs off that per-bar hook (see
    `core/__init__.py`'s docstring), and each bar's transport tick is
    always emitted *before* that same bar's script directives are
    executed, never after - reversing the order was confirmed, via a
    manual test against this exact scenario shape, to shift every
    committed op one bar early.

    Sleeps to an absolute wall-clock target (`t0 + bar*seconds_per_bar`)
    rather than a relative `time.sleep()` between ticks, so time spent
    inside Core's synchronous capture-cue completion doesn't compound as
    drift across bars.
    """

    def __init__(
        self,
        core: Core,
        scenario: Scenario,
        driver_name: str = "cli",
        on_tick: Callable[[], None] | None = None,
    ) -> None:
        self.core = core
        self.scenario = scenario
        self.driver_name = driver_name
        # Fires once per bar, after that bar's transport tick and script
        # directives are both applied - CLI monitor Tier 1 (PLAN.md §3.6)
        # hooks here to refresh a live Rich display, since this loop and
        # Core/the ring already share one process/thread; no separate
        # polling or threading needed for it.
        self.on_tick = on_tick
        self._pattern_counters: dict[int, int] = {}

    def run(self) -> None:
        self.core.handle_event(
            HelloEvent(
                driver=self.driver_name,
                caps=Capabilities(
                    clock="internal",
                    explicit_state=True,
                    capture_cues=True,
                    media_binding=False,
                    style="event",
                ),
            )
        )

        beats_per_bar = self.scenario.beats_per_bar
        seconds_per_bar = beats_per_bar * 60.0 / self.scenario.bpm
        steps_by_bar: dict[int, list[ScriptStep]] = {}
        for step in self.scenario.script:
            steps_by_bar.setdefault(step.at_bar, []).append(step)
        last_bar = max(steps_by_bar) if steps_by_bar else 0

        t0 = time.monotonic()
        playing = False
        for bar in range(last_bar + 1):
            target = t0 + bar * seconds_per_bar
            now = time.monotonic()
            if target > now:
                time.sleep(target - now)
            self.core.handle_event(
                TransportEvent(
                    playing=playing,
                    bpm=self.scenario.bpm,
                    bar=bar,
                    beat=1.0,
                    phase=0.0,
                    beats_per_bar=beats_per_bar,
                )
            )
            for step in steps_by_bar.get(bar, []):
                playing = self._execute(step, bar, playing)
            if self.on_tick is not None:
                self.on_tick()

    def _execute(self, step: ScriptStep, bar: int, playing: bool) -> bool:
        do = step.do
        if isinstance(do, str):
            return do == "play"

        if do.record is not None:
            pattern = self._pattern_counters.get(do.track, 0) + 1
            self._pattern_counters[do.track] = pattern
            self.core.handle_event(TrackEvent(track=do.track, state="recording", pattern=pattern))
            self.core.handle_event(
                CaptureCueEvent(
                    track=do.track,
                    pattern=pattern,
                    cycle_start_bar=bar,
                    bars=do.record.bars,
                )
            )
        else:
            # Re-entering "playing" carries the track's last recorded
            # pattern; every other state has nothing on the wall to name.
            pattern = self._pattern_counters.get(do.track) if do.state == "playing" else None
            self.core.handle_event(TrackEvent(track=do.track, state=do.state, pattern=pattern))
        return playing
