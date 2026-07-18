"""Monitor state model — Tier 0 (capture) and Tier 1 (transport/tracks), one shape.

Kept deliberately as one shape from the start (PLAN.md §3.6) so the render
layer never had to be redesigned when Tier 1 landed: it already knew how to
render a `MonitorState` with `transport`/`tracks` unset (Tier 0 only) or set
(Tier 1 too), because those fields were optional from the start for exactly
that reason. Tier 1 is populated only by `loopwall play <scenario> --monitor`
(`monitor_state_from_core`, below) - a standalone `loopwall monitor` process
owns its own ring and can't also share a live driver's Core, since two
processes can't open the same camera (confirmed project-wide constraint;
see `capture/ring.py`'s own docstring); the scripted `play` path already has
both the ring and a live `Core` in one process, so that's where this lands.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from loopwall.capture.ring import RingCapture
from loopwall.core.tracks import TrackRegistry
from loopwall.ldp.models import TransportEvent


@dataclass(frozen=True)
class CaptureHealth:
    is_running: bool
    segment_count: int
    ring_start: float | None
    ring_end: float | None
    ring_capacity_seconds: float
    ring_dir_bytes: int
    last_extraction_latency_ms: float | None = None

    @property
    def ring_coverage_seconds(self) -> float:
        if self.ring_start is None or self.ring_end is None:
            return 0.0
        return self.ring_end - self.ring_start


@dataclass(frozen=True)
class TransportState:  # Tier 1
    playing: bool
    bpm: float
    bar: int
    beat: float


@dataclass(frozen=True)
class TrackState:  # Tier 1
    track: int
    state: str  # empty | recording | playing | silent
    pattern: int | None = None


@dataclass(frozen=True)
class MonitorState:
    captured_at: float
    capture: CaptureHealth | None = None
    transport: TransportState | None = None
    tracks: list[TrackState] = field(default_factory=list)


def _dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(entry.stat().st_size for entry in path.rglob("*") if entry.is_file())


def capture_health_from_ring(ring: RingCapture) -> CaptureHealth:
    segs = ring.segments()
    return CaptureHealth(
        is_running=ring.is_running,
        segment_count=len(segs),
        ring_start=segs[0].start if segs else None,
        ring_end=segs[-1].end if segs else None,
        ring_capacity_seconds=ring.ring_seconds,
        ring_dir_bytes=_dir_size_bytes(ring.ring_dir),
    )


def capture_monitor_state(ring: RingCapture) -> MonitorState:
    return MonitorState(captured_at=time.time(), capture=capture_health_from_ring(ring))


def monitor_state_from_core(
    ring: RingCapture, tracks: TrackRegistry, last_transport: TransportEvent | None
) -> MonitorState:
    """Tier 0 + Tier 1 together - `tracks`/`last_transport` read straight off a live
    `Core` (`core.tracks`, `core.last_transport`), not a second state machine tracking
    the same thing (PLAN.md §3.6: "the monitor can never show something core didn't
    actually commit"). Takes the raw registry/event rather than a `Core` object so
    this stays testable without constructing one, matching `capture_health_from_ring`'s
    own convention of taking `RingCapture` directly.
    """
    transport = None
    if last_transport is not None:
        transport = TransportState(
            playing=last_transport.playing,
            bpm=last_transport.bpm,
            bar=last_transport.bar,
            beat=last_transport.beat,
        )
    return MonitorState(
        captured_at=time.time(),
        capture=capture_health_from_ring(ring),
        transport=transport,
        tracks=[TrackState(track=t.track, state=t.state, pattern=t.pattern) for t in tracks.all()],
    )
