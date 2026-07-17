"""Monitor state model — Tier 0 (capture) populated now, Tier 1 (transport/tracks) reserved.

Kept deliberately as one shape from the start (PLAN.md §3.6) so the render
layer never has to be redesigned when Tier 1 lands: it already knows how to
render a `MonitorState` with `transport`/`tracks` unset (Tier 0) or set
(Tier 1), because those fields are optional now for exactly that reason.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from loopwall.capture.ring import RingCapture


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
class TransportState:  # Tier 1 — reserved, not populated until core/timeline exists.
    playing: bool
    bpm: float
    bar: int
    beat: float


@dataclass(frozen=True)
class TrackState:  # Tier 1 — reserved, not populated until core/tracks exists.
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
