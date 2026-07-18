from pathlib import Path

from loopwall.capture.ring import RingCapture
from loopwall.core.tracks import TrackRegistry
from loopwall.ldp.models import TransportEvent
from loopwall.monitor.state import capture_health_from_ring, capture_monitor_state, monitor_state_from_core


def _ring(tmp_path: Path) -> RingCapture:
    # Never started - this exercises segments()/disk usage against a
    # hand-written ring directory, no ffmpeg/subprocess involved.
    return RingCapture(ring_dir=tmp_path / "ring", input_args=[], ring_seconds=20.0)


def test_capture_health_with_no_segments_yet(tmp_path: Path) -> None:
    ring = _ring(tmp_path)
    ring.ring_dir.mkdir(parents=True)

    health = capture_health_from_ring(ring)

    assert health.segment_count == 0
    assert health.ring_start is None
    assert health.ring_end is None
    assert health.ring_coverage_seconds == 0.0
    assert health.ring_capacity_seconds == 20.0
    assert not health.is_running


def test_capture_health_reads_segment_list(tmp_path: Path) -> None:
    ring = _ring(tmp_path)
    ring.ring_dir.mkdir(parents=True)
    (ring.ring_dir / "list.csv").write_text(
        "seg_000.mp4,0.000000,2.000000\nseg_001.mp4,2.000000,4.000000\nseg_002.mp4,4.000000,6.000000\n"
    )
    (ring.ring_dir / "seg_000.mp4").write_bytes(b"x" * 1000)

    health = capture_health_from_ring(ring)

    assert health.segment_count == 3
    assert health.ring_start == 0.0
    assert health.ring_end == 6.0
    assert health.ring_coverage_seconds == 6.0
    assert health.ring_dir_bytes == 1000 + len((ring.ring_dir / "list.csv").read_bytes())


def test_capture_health_dedupes_wrapped_segments(tmp_path: Path) -> None:
    """The segment list is append-only across ring wraps - only the latest
    row for a given filename reflects what's actually on disk right now
    (RingCapture.segments()'s own contract; see its docstring)."""
    ring = _ring(tmp_path)
    ring.ring_dir.mkdir(parents=True)
    (ring.ring_dir / "list.csv").write_text(
        "seg_000.mp4,0.000000,2.000000\n"
        "seg_001.mp4,2.000000,4.000000\n"
        "seg_000.mp4,4.000000,6.000000\n"  # wrapped - seg_000.mp4 now holds this
    )

    health = capture_health_from_ring(ring)

    assert health.segment_count == 2  # not 3
    assert health.ring_start == 2.0  # seg_001's start, since seg_000's old row is stale
    assert health.ring_end == 6.0


def test_capture_monitor_state_wraps_capture_health(tmp_path: Path) -> None:
    ring = _ring(tmp_path)
    ring.ring_dir.mkdir(parents=True)

    state = capture_monitor_state(ring)

    assert state.capture is not None
    assert state.transport is None
    assert state.tracks == []
    assert state.captured_at > 0


def test_monitor_state_from_core_with_no_transport_yet(tmp_path: Path) -> None:
    ring = _ring(tmp_path)
    ring.ring_dir.mkdir(parents=True)

    state = monitor_state_from_core(ring, TrackRegistry(), last_transport=None)

    assert state.capture is not None
    assert state.transport is None
    assert state.tracks == []


def test_monitor_state_from_core_reflects_live_tracks_and_transport(tmp_path: Path) -> None:
    ring = _ring(tmp_path)
    ring.ring_dir.mkdir(parents=True)
    tracks = TrackRegistry()
    tracks.apply(1, "recording", pattern=1)
    tracks.apply(2, "playing", pattern=3)
    transport = TransportEvent(playing=True, bpm=118.0, bar=7, beat=2.5, phase=0.25, beats_per_bar=4)

    state = monitor_state_from_core(ring, tracks, last_transport=transport)

    assert state.transport is not None
    assert state.transport.playing is True
    assert state.transport.bpm == 118.0
    assert state.transport.bar == 7
    assert state.transport.beat == 2.5
    assert {(t.track, t.state, t.pattern) for t in state.tracks} == {(1, "recording", 1), (2, "playing", 3)}
