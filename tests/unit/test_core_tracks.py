from loopwall.core.tracks import Track, TrackRegistry


def test_unseen_track_defaults_to_empty() -> None:
    registry = TrackRegistry()
    assert registry.get(5) == Track(track=5, state="empty", pattern=None)


def test_apply_returns_old_and_new() -> None:
    registry = TrackRegistry()
    old, new = registry.apply(1, "recording", pattern=1)
    assert old == Track(track=1, state="empty", pattern=None)
    assert new == Track(track=1, state="recording", pattern=1)
    assert registry.get(1) == new


def test_apply_again_returns_previous_as_old() -> None:
    registry = TrackRegistry()
    registry.apply(1, "recording", pattern=1)
    old, new = registry.apply(1, "playing", pattern=1)
    assert old == Track(track=1, state="recording", pattern=1)
    assert new == Track(track=1, state="playing", pattern=1)


def test_all_lists_only_touched_tracks() -> None:
    registry = TrackRegistry()
    registry.apply(2, "recording", pattern=1)
    assert registry.all() == [Track(track=2, state="recording", pattern=1)]
