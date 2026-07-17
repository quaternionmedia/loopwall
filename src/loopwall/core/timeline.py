"""Bar-boundary wall-clock timestamps (bar -> t_mono).

Built at 0.1.0 build order step 3 (ROADMAP.md).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _Breakpoint:
    bar: float
    t_mono: float
    bpm: float
    beats_per_bar: int


class Timeline:
    """Bar <-> wall-clock-time mapping, correct across tempo changes.

    A tempo/meter change is a new breakpoint (bar, t_mono, bpm,
    beats_per_bar); bars before it resolve against the *prior* breakpoint,
    so an already-elapsed bar's timestamp never gets retroactively
    distorted by a later tempo change - the standard piecewise
    tempo-map construction. :meth:`on_transport` only appends when
    something actually changed - redundant appends inside a
    constant-tempo stretch (the driver ticks every bar) would grow the
    list for no benefit, since extrapolating from any breakpoint within a
    constant-tempo run gives an identical answer.

    Time-signature changes generalize the same way (just another
    breakpoint field) but nothing in the 0.1.0 scenario schema exercises
    one - deliberately deferred, not silently missing. `bpm` is always
    quarter-notes/minute regardless of the meter's denominator (6/8 etc.
    is out of scope; not required by anything in ROADMAP.md's 0.1.0 list).
    """

    def __init__(self) -> None:
        self._breakpoints: list[_Breakpoint] = []

    def on_transport(self, bar: float, t_mono: float, bpm: float, beats_per_bar: int = 4) -> None:
        if self._breakpoints and bar < self._breakpoints[-1].bar:
            raise ValueError(f"bar {bar} precedes the last breakpoint's bar {self._breakpoints[-1].bar}")
        if self._breakpoints:
            last = self._breakpoints[-1]
            if last.bpm == bpm and last.beats_per_bar == beats_per_bar:
                return
        self._breakpoints.append(_Breakpoint(bar, t_mono, bpm, beats_per_bar))

    def _at_bar(self, bar: float) -> _Breakpoint:
        if not self._breakpoints:
            raise ValueError("no breakpoints recorded yet - call on_transport() first")
        candidate = self._breakpoints[0]
        for bp in self._breakpoints:
            if bp.bar > bar:
                break
            candidate = bp
        return candidate

    def _at_time(self, t: float) -> _Breakpoint:
        if not self._breakpoints:
            raise ValueError("no breakpoints recorded yet - call on_transport() first")
        candidate = self._breakpoints[0]
        for bp in self._breakpoints:
            if bp.t_mono > t:
                break
            candidate = bp
        return candidate

    def bar_to_time(self, bar: float) -> float:
        bp = self._at_bar(bar)
        seconds_per_bar = bp.beats_per_bar * 60.0 / bp.bpm
        return bp.t_mono + (bar - bp.bar) * seconds_per_bar

    def time_to_bar(self, t: float) -> float:
        bp = self._at_time(t)
        seconds_per_bar = bp.beats_per_bar * 60.0 / bp.bpm
        return bp.bar + (t - bp.t_mono) / seconds_per_bar

    def bar_beat_phase_at(self, t: float) -> tuple[int, float, float]:
        """(bar, beat, phase) at wall-clock time t.

        beat is a continuous, 1-indexed position within the bar (e.g.
        2.5 - halfway through beat 2); phase is the fraction of the
        current *bar* elapsed (0.0-1.0), matching how "phase" is already
        used elsewhere in this project's docs (a repeating-cycle
        position), not a separate per-beat phase.
        """
        bar_float = self.time_to_bar(t)
        bp = self._at_time(t)
        bar = int(bar_float)
        frac_bar = bar_float - bar
        beat = 1.0 + frac_bar * bp.beats_per_bar
        return bar, beat, frac_bar
