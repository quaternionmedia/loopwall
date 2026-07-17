"""Quantize scheduler: commits grid ops at the configured boundary.

Built at 0.1.0 build order step 4 (ROADMAP.md).
"""

from __future__ import annotations

from pydantic import BaseModel

_ON_WALL = {"recording", "playing"}


class CommittedOp(BaseModel):
    kind: str  # add | remove | swap | reflow (PLAN.md §3.3)
    track: int
    bar: int
    pattern: int | None = None


class Scheduler:
    """Presence-based grid-op rule: an op fires only when a track's presence on the wall flips.

    `recording`/`playing` count as on the wall; `empty`/`silent` don't. A
    `recording -> playing` transition (a take finishing) is presence-
    preserving (True -> True) and produces **no op at all** - this is why
    `scenarios/smoke.yaml`'s `committed_ops_on_bars: [2, 8, 12, 14]`
    excludes bars 6 and 10, where each track's take completes: it isn't
    that those bars are merely unlisted, nothing is emitted there.

    `swap`/`reflow` are reserved for 0.2.0's seat-assignment logic
    (STUBS.md: "layout / seat assignment | absent; scheduler emits
    track-level ops only") - this scheduler can only ever produce
    `add`/`remove`.
    """

    def __init__(self) -> None:
        self.committed: list[CommittedOp] = []

    def on_track_state_change(
        self, track: int, old_state: str, new_state: str, bar: int, pattern: int | None
    ) -> CommittedOp | None:
        was_on_wall = old_state in _ON_WALL
        is_on_wall = new_state in _ON_WALL
        if was_on_wall == is_on_wall:
            return None
        op = CommittedOp(kind="add" if is_on_wall else "remove", track=track, bar=bar, pattern=pattern)
        self.committed.append(op)
        return op
