"""Track registry: per-track state as last declared by the driver.

Built at 0.1.0 build order step 4 (ROADMAP.md).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Track:
    # Field names match monitor.state.TrackState on purpose - a friction-
    # free mapping once CLI monitor Tier 1 (ROADMAP.md step 10) lands.
    track: int
    state: str  # empty | recording | playing | silent
    pattern: int | None = None


class TrackRegistry:
    """Per-track state as last declared by the driver.

    Console overrides are masks applied after driver truth, never
    mutations of it (PLAN.md §3.3) - out of scope at 0.1.0 (no console
    exists yet), so this registry only ever holds driver-declared truth.
    An unseen track defaults to "empty" rather than raising - a driver is
    free to reference a track for the first time via any state change.
    """

    def __init__(self) -> None:
        self._tracks: dict[int, Track] = {}

    def apply(self, track: int, state: str, pattern: int | None = None) -> tuple[Track, Track]:
        """Update track's state. Returns (old, new)."""
        old = self.get(track)
        new = Track(track=track, state=state, pattern=pattern)
        self._tracks[track] = new
        return old, new

    def get(self, track: int) -> Track:
        return self._tracks.get(track, Track(track=track, state="empty", pattern=None))

    def all(self) -> list[Track]:
        return list(self._tracks.values())
