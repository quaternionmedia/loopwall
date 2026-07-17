"""Replay determinism: same driver log in => same committed-op sequence out.

ROADMAP.md build order step 4's own named occupant for this directory.
Tracks/scheduler only - no ffmpeg, no full Core.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from loopwall.core.scheduler import Scheduler
from loopwall.core.tracks import TrackRegistry

_STATES = ["empty", "recording", "playing", "silent"]

_directives = st.lists(
    st.tuples(
        st.integers(min_value=1, max_value=4),  # track
        st.sampled_from(_STATES),
        st.one_of(st.none(), st.integers(min_value=1, max_value=3)),  # pattern
    ),
    max_size=40,
)


def _replay(directives: list[tuple[int, str, int | None]]) -> list[tuple[str, int, int, int | None]]:
    tracks = TrackRegistry()
    scheduler = Scheduler()
    for bar, (track, state, pattern) in enumerate(directives):
        old, new = tracks.apply(track, state, pattern)
        scheduler.on_track_state_change(track, old.state, new.state, bar, pattern)
    return [(op.kind, op.track, op.bar, op.pattern) for op in scheduler.committed]


@given(_directives)
def test_replay_is_deterministic(directives: list[tuple[int, str, int | None]]) -> None:
    first = _replay(directives)
    second = _replay(directives)
    assert first == second


@given(_directives)
def test_committed_ops_only_ever_flip_presence(directives: list[tuple[int, str, int | None]]) -> None:
    """Every committed op corresponds to an actual empty/silent <-> recording/playing crossing."""
    on_wall = {"recording", "playing"}
    tracks = TrackRegistry()
    scheduler = Scheduler()
    for bar, (track, state, pattern) in enumerate(directives):
        old, new = tracks.apply(track, state, pattern)
        op = scheduler.on_track_state_change(track, old.state, new.state, bar, pattern)
        was_on_wall = old.state in on_wall
        is_on_wall = new.state in on_wall
        if was_on_wall == is_on_wall:
            assert op is None
        else:
            assert op is not None
            assert op.kind == ("add" if is_on_wall else "remove")
