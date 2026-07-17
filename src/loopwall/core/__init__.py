"""Driver-agnostic, deterministic core. See PLAN.md §3.3.

Built at 0.1.0 build order steps 3-4, 8 come together here: Timeline +
TrackRegistry + Scheduler + SessionArtifact + the capture service, wired
behind one method (`handle_event`) any driver can call - in-process today
(the `cli` driver), over `/driver` WebSocket once that transport exists
(server/app.py stays a stub for now; PLAN.md §3.1 treats in-process as a
first-class driver-connection mode, not a shortcut).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from loopwall.capture.extract import (
    DurationCompensator,
    ExtractionError,
    extract_window,
    probe_duration,
    wait_for_coverage,
)
from loopwall.capture.ring import RingCapture
from loopwall.core import reconciler
from loopwall.core.scheduler import Scheduler
from loopwall.core.session import SessionArtifact
from loopwall.core.timeline import Timeline
from loopwall.core.tracks import TrackRegistry
from loopwall.ldp.models import LdpEvent
from loopwall.ldp.wire import dump_event


@dataclass(frozen=True)
class _PendingCapture:
    track: int
    pattern: int
    cycle_start_bar: int
    close_bar: int
    camera: str | None


class Core:
    """One entry point - `handle_event` - driving timeline/tracks/scheduler/session/capture together.

    `extract_fn` is injectable, defaulting to the real `extract_window` -
    the same pattern `demo.py` already uses for its player functions, so
    unit tests can spy on extraction without spawning ffmpeg while
    integration tests use the real one. A single `ring: RingCapture`, not
    a dict keyed by camera id - PLAN.md §4's own ratified default is
    "single camera" for 0.1.0; a `capture_cue.camera` that doesn't match
    `default_camera_id` is a clear error, not something to key a lookup on.

    `capture_cue` completion is synchronous, inside `handle_event` - x264
    `veryfast` on a few-second clip is well inside the plan's <=3s budget;
    async scheduling is a 0.2.0-renderer concern (keeping a tick loop
    unblocked only matters once something is watching it live), not built
    speculatively now.
    """

    def __init__(
        self,
        timeline: Timeline,
        tracks: TrackRegistry,
        scheduler: Scheduler,
        session: SessionArtifact,
        ring: RingCapture,
        default_camera_id: str = "cam0",
        extract_fn: Callable[[RingCapture, float, float, Path], Path] = extract_window,
    ) -> None:
        self.timeline = timeline
        self.tracks = tracks
        self.scheduler = scheduler
        self.session = session
        self.ring = ring
        self.default_camera_id = default_camera_id
        self.extract_fn = extract_fn
        self._pending: list[_PendingCapture] = []
        self._compensator = DurationCompensator()
        self._current_bar = 0

    def handle_event(self, event: LdpEvent) -> None:
        # Logged first, before any type-specific handling, so the log
        # stays complete/replayable regardless of what handling exists.
        self.session.log_event(dump_event(event))
        match event.t:
            case "hello":
                pass
            case "transport":
                self._on_transport(event)
            case "track":
                self._on_track(event)
            case "capture_cue":
                self._on_capture_cue(event)
            case "scene":
                reconciler.reconcile(event)  # registered stub - raises (STUBS.md, target 1.1.0)
            case "accent" | "clip_bind" | "config_hint":
                pass  # logged above; real log-and-drop at 0.1.0, not a placeholder (STUBS.md)

    def _on_transport(self, event: LdpEvent) -> None:
        self.timeline.on_transport(event.bar, time.monotonic(), event.bpm, event.beats_per_bar)
        self._current_bar = event.bar
        due = [p for p in self._pending if p.close_bar <= event.bar]
        self._pending = [p for p in self._pending if p.close_bar > event.bar]
        for pending in due:
            self._complete_capture(pending)

    def _on_track(self, event: LdpEvent) -> None:
        old, new = self.tracks.apply(event.track, event.state, event.pattern)
        self._commit_if_changed(event.track, old.state, new.state, new.pattern)

    def _on_capture_cue(self, event: LdpEvent) -> None:
        self._pending.append(
            _PendingCapture(
                track=event.track,
                pattern=event.pattern,
                cycle_start_bar=event.cycle_start_bar,
                close_bar=event.cycle_start_bar + event.bars,
                camera=event.camera,
            )
        )

    def _commit_if_changed(self, track: int, old_state: str, new_state: str, pattern: int | None) -> None:
        op = self.scheduler.on_track_state_change(track, old_state, new_state, self._current_bar, pattern)
        if op is not None:
            self.session.log_committed_op(op.model_dump())

    def _complete_capture(self, pending: _PendingCapture) -> None:
        t_trigger = time.monotonic()
        if self.ring.t0 is None:
            raise RuntimeError("ring must be started before a capture_cue can complete")
        t_a = self.timeline.bar_to_time(pending.cycle_start_bar) - self.ring.t0
        t_b = self.timeline.bar_to_time(pending.close_bar) - self.ring.t0
        t_a, t_b = self._compensator.compensate(t_a, t_b)

        # 10s, not the ≤3s this class's own docstring once assumed was
        # plenty - a real-hardware run (1280x720, back-to-back captures)
        # showed a synchronous extraction's own CPU use can contend with
        # the ring's concurrent encode for long enough that its segment
        # flush lags a plain 3s budget, even though the extraction itself
        # (x264 veryfast on an 8s clip) finished in ~3.2s. This is a
        # symptom of the same synchronous-completion design the docstring
        # already flags as a 0.2.0-renderer concern, not a fix to it.
        if not wait_for_coverage(self.ring, t_b, timeout=10.0):
            raise ExtractionError(f"ring never caught up to cover t_b={t_b}")
        out_path, take = self.session.allocate_clip_path(pending.track, pending.pattern)
        # No degradation ladder exists yet (PLAN.md §3.5 is 0.2.0+); a
        # failed extraction propagates as a hard error, not masked.
        self.extract_fn(self.ring, t_a, t_b, out_path)
        actual_duration = probe_duration(out_path)
        self._compensator.observe(t_b - t_a, actual_duration)

        latency_ms = (time.monotonic() - t_trigger) * 1000
        self.session.register_clip(
            track=pending.track,
            pattern=pending.pattern,
            take=take,
            path=out_path,
            cycle_start_bar=pending.cycle_start_bar,
            bars=pending.close_bar - pending.cycle_start_bar,
            duration_seconds=actual_duration,
            extraction_latency_ms=latency_ms,
            camera=pending.camera or self.default_camera_id,
        )

        old, new = self.tracks.apply(pending.track, "playing", pending.pattern)
        self._commit_if_changed(pending.track, old.state, new.state, new.pattern)
