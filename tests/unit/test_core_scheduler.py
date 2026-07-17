import pytest

from loopwall.core.scheduler import Scheduler

# (old_state, new_state) -> expected op kind, or None if no op should fire.
# This is the presence-based rule's own truth table: an op fires only when
# a track's wall-presence flips between {empty,silent} and
# {recording,playing} - same-bucket transitions (including recording <->
# playing) are presence-preserving and produce no op at all.
_CASES = [
    ("empty", "recording", "add"),
    ("empty", "playing", "add"),
    ("silent", "recording", "add"),
    ("silent", "playing", "add"),
    ("recording", "playing", None),
    ("playing", "recording", None),
    ("recording", "silent", "remove"),
    ("recording", "empty", "remove"),
    ("playing", "silent", "remove"),
    ("playing", "empty", "remove"),
    ("empty", "silent", None),
    ("silent", "empty", None),
    ("empty", "empty", None),
    ("recording", "recording", None),
]


@pytest.mark.parametrize(("old_state", "new_state", "expected_kind"), _CASES)
def test_presence_flip_truth_table(old_state: str, new_state: str, expected_kind: str | None) -> None:
    scheduler = Scheduler()
    op = scheduler.on_track_state_change(track=1, old_state=old_state, new_state=new_state, bar=3, pattern=1)
    if expected_kind is None:
        assert op is None
        assert scheduler.committed == []
    else:
        assert op is not None
        assert op.kind == expected_kind
        assert op.track == 1
        assert op.bar == 3
        assert op.pattern == 1
        assert scheduler.committed == [op]


def test_committed_ops_accumulate_in_order() -> None:
    scheduler = Scheduler()
    scheduler.on_track_state_change(1, "empty", "recording", bar=2, pattern=1)
    scheduler.on_track_state_change(2, "empty", "recording", bar=8, pattern=1)
    scheduler.on_track_state_change(1, "recording", "silent", bar=12, pattern=1)
    kinds_and_bars = [(op.kind, op.bar) for op in scheduler.committed]
    assert kinds_and_bars == [("add", 2), ("add", 8), ("remove", 12)]
