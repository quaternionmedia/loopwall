import pytest

from loopwall.core.timeline import Timeline


def test_bar_to_time_and_back_at_constant_tempo() -> None:
    tl = Timeline()
    tl.on_transport(bar=0, t_mono=100.0, bpm=120.0, beats_per_bar=4)
    # 120bpm, 4/4 -> 2.0s/bar
    assert tl.bar_to_time(1) == pytest.approx(102.0)
    assert tl.bar_to_time(2) == pytest.approx(104.0)
    assert tl.time_to_bar(104.0) == pytest.approx(2.0)


def test_redundant_ticks_at_same_tempo_do_not_add_breakpoints() -> None:
    tl = Timeline()
    tl.on_transport(bar=0, t_mono=100.0, bpm=120.0, beats_per_bar=4)
    tl.on_transport(bar=1, t_mono=102.0, bpm=120.0, beats_per_bar=4)
    tl.on_transport(bar=2, t_mono=104.0, bpm=120.0, beats_per_bar=4)
    assert len(tl._breakpoints) == 1


def test_tempo_change_does_not_retroactively_distort_earlier_bars() -> None:
    tl = Timeline()
    tl.on_transport(bar=0, t_mono=100.0, bpm=120.0, beats_per_bar=4)
    before_change = tl.bar_to_time(1)
    # Tempo doubles at bar 2.
    tl.on_transport(bar=2, t_mono=104.0, bpm=240.0, beats_per_bar=4)
    after_change = tl.bar_to_time(1)
    assert before_change == pytest.approx(after_change) == pytest.approx(102.0)
    # 240bpm, 4/4 -> 1.0s/bar from bar 2 onward.
    assert tl.bar_to_time(3) == pytest.approx(105.0)


def test_bar_to_time_before_any_transport_raises() -> None:
    tl = Timeline()
    with pytest.raises(ValueError, match="no breakpoints"):
        tl.bar_to_time(0)


def test_out_of_order_bar_raises() -> None:
    tl = Timeline()
    tl.on_transport(bar=4, t_mono=100.0, bpm=120.0, beats_per_bar=4)
    with pytest.raises(ValueError, match="precedes"):
        tl.on_transport(bar=2, t_mono=102.0, bpm=120.0, beats_per_bar=4)


def test_bar_beat_phase_at() -> None:
    tl = Timeline()
    tl.on_transport(bar=0, t_mono=100.0, bpm=120.0, beats_per_bar=4)
    # Halfway through bar 0 (1.0s in, of a 2.0s bar) -> beat 3.0, phase 0.5.
    bar, beat, phase = tl.bar_beat_phase_at(101.0)
    assert bar == 0
    assert beat == pytest.approx(3.0)
    assert phase == pytest.approx(0.5)
