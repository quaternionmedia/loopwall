import io

from rich.console import Console

from loopwall.monitor.render import render
from loopwall.monitor.state import CaptureHealth, MonitorState, TrackState, TransportState


def _rendered_text(state: MonitorState) -> str:
    console = Console(file=io.StringIO(), width=100, no_color=True)
    console.print(render(state))
    return console.file.getvalue()


def test_render_with_no_capture_attached() -> None:
    text = _rendered_text(MonitorState(captured_at=0.0))
    assert "no capture attached" in text


def test_render_shows_capture_health_fields() -> None:
    state = MonitorState(
        captured_at=0.0,
        capture=CaptureHealth(
            is_running=True,
            segment_count=5,
            ring_start=10.0,
            ring_end=20.0,
            ring_capacity_seconds=120.0,
            ring_dir_bytes=2_500_000,
            last_extraction_latency_ms=812.5,
        ),
    )
    text = _rendered_text(state)

    assert "yes" in text  # running
    assert "5" in text  # segment count
    assert "10.00s" in text and "20.00s" in text  # coverage window
    assert "10.0s" in text  # coverage duration (20.0 - 10.0)
    assert "120s" in text  # capacity
    assert "2.4 MB" in text  # ring_dir_bytes formatted
    assert "812ms" in text  # extraction latency


def test_render_shows_not_running_state() -> None:
    state = MonitorState(
        captured_at=0.0,
        capture=CaptureHealth(
            is_running=False,
            segment_count=0,
            ring_start=None,
            ring_end=None,
            ring_capacity_seconds=120.0,
            ring_dir_bytes=0,
        ),
    )
    text = _rendered_text(state)
    assert "no" in text
    assert "no segments yet" in text


def test_render_tier1_panel_appears_only_once_transport_or_tracks_present() -> None:
    without_tier1 = _rendered_text(MonitorState(captured_at=0.0))
    assert "arrangement" not in without_tier1

    with_transport = _rendered_text(
        MonitorState(captured_at=0.0, transport=TransportState(playing=True, bpm=120.0, bar=1, beat=1.0))
    )
    assert "arrangement" in with_transport

    with_tracks = _rendered_text(MonitorState(captured_at=0.0, tracks=[TrackState(track=1, state="playing")]))
    assert "arrangement" in with_tracks


def test_render_shows_transport_and_track_fields() -> None:
    state = MonitorState(
        captured_at=0.0,
        transport=TransportState(playing=True, bpm=118.0, bar=7, beat=2.5),
        tracks=[
            TrackState(track=1, state="playing", pattern=1),
            TrackState(track=2, state="recording", pattern=2),
            TrackState(track=3, state="empty"),
        ],
    )
    text = _rendered_text(state)

    assert "playing" in text
    assert "bar 7" in text
    assert "beat 2.5" in text
    assert "118.0bpm" in text
    assert "track 1" in text and "(pattern 1)" in text
    assert "track 2" in text and "recording" in text and "(pattern 2)" in text
    assert "track 3" in text and "empty" in text
