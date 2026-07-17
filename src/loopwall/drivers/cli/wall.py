"""Wall driver: the `cli` driver's REPL half. Live camera preview, a toggle
control starts/stops recording, and each stop adds a new permanently
looping tile to a growing grid - real LDP events into `Core`, not a
bypass. See PLAN.md §3.5, §3.7.

Async, not a synchronous keypress-poll loop - the control surface moved
into the browser itself (`server/app.py`'s `/wall` page), so this class is
driven by a FastAPI app's lifespan/request handlers sharing one asyncio
event loop, not a blocking terminal loop with its own keyboard polling.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from loopwall.capture.ring import RingCapture
from loopwall.core import Core
from loopwall.ldp.models import (
    Capabilities,
    CaptureCueEvent,
    HelloEvent,
    LdpEvent,
    TrackEvent,
    TransportEvent,
)
from loopwall.monitor.state import capture_health_from_ring
from loopwall.server.relay import PreviewRelay

TICK_SECONDS = 0.2  # internal quantization clock - not a musical tempo, see WallDriver's docstring
MIN_RECORD_SECONDS = 1.0  # ignore a stop pressed before there's at least this much to cut


@dataclass
class Tile:
    track: int
    state: str  # "playing" - the only state a tile is ever created with, see WallDriver.toggle()
    clip_url: str


class WallDriver:
    """Owns `Core`, the ring, and the live-preview relay.

    Serves state to and takes control from the wall page.

    Bar/tempo are an internal quantization unit here, not a musical
    concept - `Core`'s capture-cue mechanism needs *some* bar granularity
    to close a window on, so each tick counts as one bar, with its
    nominal bpm computed from the *actual* elapsed time since the
    previous tick. That forces `core/timeline.py` to record an
    exactly-real-time breakpoint every tick rather than extrapolating
    from one fixed rate - there's no fixed rate here to drift from in the
    first place.

    `core.handle_event` is synchronous and can block for seconds (a
    capture's extraction re-encode) - every call to it goes through
    `_dispatch_locked`, run in a thread pool so the event loop (and
    therefore `/wall/state` polling and `/wall/control` toggles) stays
    responsive, serialized by `_core_lock` so the periodic ticker and a
    toggle action never call into `Core` concurrently.
    """

    def __init__(
        self, core: Core, ring: RingCapture, relay: PreviewRelay, driver_name: str = "cli-wall"
    ) -> None:
        self.core = core
        self.ring = ring
        self.relay = relay
        self.driver_name = driver_name
        self.tiles: list[Tile] = []
        self._next_track = 1
        self._recording_track: int | None = None
        self._record_started_at: float | None = None
        self._start_bar: int | None = None
        self._bar = 0
        self._t_prev = 0.0
        self._core_lock = asyncio.Lock()
        self._ticker_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self.ring.preview_tcp_port is None:
            raise ValueError("WallDriver requires a RingCapture built with preview_tcp_port set")

        await self.relay.start(self.ring.preview_tcp_port)
        self.ring.start()

        await self._dispatch(
            HelloEvent(
                driver=self.driver_name,
                caps=Capabilities(
                    clock="internal",
                    explicit_state=True,
                    capture_cues=True,
                    media_binding=False,
                    style="event",
                ),
            )
        )
        self._t_prev = time.monotonic()
        await self._tick(playing=False)
        self._running = True
        self._ticker_task = asyncio.create_task(self._run_ticker())

    async def stop(self) -> None:
        self._running = False
        if self._ticker_task is not None:
            self._ticker_task.cancel()
            self._ticker_task = None
        await self.relay.stop()
        self.ring.stop()

    async def toggle(self) -> None:
        """Start recording a new track, or - if one's already recording - stop and cut it.

        A track only ever appears in `self.tiles` once its clip exists -
        while it's recording, the wall page's own always-on "pending" tile
        (driven by `state()`'s `recording`/`record_seconds`) already shows
        the live feed, so a second, redundant tile pointed at the same
        MJPEG stream would just be another connection to the same relay
        for no visible difference - the earlier version did exactly that,
        and it also meant a stream that outlived its tile whenever the
        browser removed that element without the server ever finding out.
        """
        async with self._core_lock:
            if self._recording_track is None:
                track = self._next_track
                self._next_track += 1
                self._recording_track = track
                self._start_bar = self._bar
                self._record_started_at = time.monotonic()
                await self._dispatch_locked(TrackEvent(track=track, state="recording", pattern=1))
            elif time.monotonic() - self._record_started_at >= MIN_RECORD_SECONDS:
                track = self._recording_track
                await self._dispatch_locked(
                    CaptureCueEvent(
                        track=track,
                        pattern=1,
                        cycle_start_bar=self._start_bar,
                        bars=self._bar - self._start_bar,
                    )
                )
                # One more tick closes the window right now - Core's
                # capture completion is synchronous.
                await self._tick_locked(playing=True)
                clip_path = Path(self.core.session.clips[-1].path)
                self.tiles.append(Tile(track=track, state="playing", clip_url=f"/clips/{clip_path.name}"))
                self._recording_track = None
                self._start_bar = None
                self._record_started_at = None

    async def _run_ticker(self) -> None:
        while self._running:
            await asyncio.sleep(TICK_SECONDS)
            async with self._core_lock:
                await self._tick_locked(playing=self._recording_track is not None)

    async def _tick(self, playing: bool) -> None:
        async with self._core_lock:
            await self._tick_locked(playing)

    async def _tick_locked(self, playing: bool) -> None:
        now = time.monotonic()
        elapsed = max(now - self._t_prev, 1e-6)
        self._bar += 1
        bpm = 60.0 / elapsed  # beats_per_bar=1, so seconds_per_bar == elapsed exactly
        await self._dispatch_locked(
            TransportEvent(playing=playing, bpm=bpm, bar=self._bar, beat=1.0, phase=0.0, beats_per_bar=1)
        )
        self._t_prev = now

    async def _dispatch(self, event: LdpEvent) -> None:
        async with self._core_lock:
            await self._dispatch_locked(event)

    async def _dispatch_locked(self, event: LdpEvent) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.core.handle_event, event)

    def state(self) -> dict:
        health = capture_health_from_ring(self.ring)
        return {
            "tiles": [{"track": t.track, "state": t.state, "clip_url": t.clip_url} for t in self.tiles],
            "recording": self._recording_track is not None,
            "record_seconds": (
                time.monotonic() - self._record_started_at if self._record_started_at is not None else None
            ),
            "ring": {
                "running": health.is_running,
                "segments": health.segment_count,
                "coverage_seconds": health.ring_coverage_seconds,
            },
        }
