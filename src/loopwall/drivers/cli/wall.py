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
import functools
import sys
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
from loopwall.midi import MidiInput
from loopwall.monitor.state import capture_health_from_ring
from loopwall.server.relay import PreviewRelay

TICK_SECONDS = 0.2  # internal quantization clock, used while no external MIDI clock is active
MIN_RECORD_SECONDS = 1.0  # ignore a stop pressed before there's at least this much to cut


def _quiet_benign_connection_reset(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Windows' ProactorEventLoop logs an "Exception in callback" from its own internal
    transport teardown (_call_connection_lost) whenever a TCP peer disappears via RST
    instead of a clean FIN - e.g. ring.stop()'s taskkill /T killing ffmpeg mid-connection
    to PreviewRelay. It's noise, not a real failure (PreviewRelay._on_connect already
    handles the same condition on its own read path) - everything else still surfaces
    normally via the default handler.
    """
    if isinstance(context.get("exception"), ConnectionResetError):
        return
    loop.default_exception_handler(context)


@dataclass
class Tile:
    track: int
    state: str  # "playing" - the only state a tile is ever created with, see WallDriver._apply_pending
    clip_url: str
    duration_seconds: float


class WallDriver:
    """Owns `Core`, the ring, the live-preview relay, and (optionally) a MIDI input.

    Serves state to and takes control from the wall page.

    Bar/tempo are an internal quantization unit here when nothing external
    is driving them, not a musical concept - `Core`'s capture-cue
    mechanism needs *some* bar granularity to close a window on, so each
    tick counts as one bar, with its nominal bpm computed from the
    *actual* elapsed time since the previous tick. That forces
    `core/timeline.py` to record an exactly-real-time breakpoint every
    tick rather than extrapolating from one fixed rate - there's no fixed
    rate here to drift from in the first place. When a real external MIDI
    clock is active (`self.midi.clock.is_active()`), it drives bar
    boundaries instead - see `_on_midi_bar` - and the internal ticker
    steps aside (`_run_ticker`'s own check) rather than racing it.

    A toggle (keyboard, browser click, or a MIDI trigger) never acts
    immediately - it *arms* a start or stop, applied on the next bar tick
    from whichever clock is currently live. This is what makes "armed" a
    real, visible state rather than a label that's only meaningful with
    an external clock connected; with the fast internal ticker it only
    ever lasts a fraction of a second, but the state machine underneath
    is the same either way, and it's what a MIDI clock's quantization
    actually needs to hook into.

    `core.handle_event` is synchronous and can block for seconds (a
    capture's extraction re-encode) - every call to it goes through
    `_dispatch_locked`, run in a thread pool so the event loop (and
    therefore `/wall/state` polling and `/wall/control` actions) stays
    responsive, serialized by `_core_lock` so the ticker, a MIDI bar
    boundary, and a control action never call into `Core` concurrently.
    """

    def __init__(
        self,
        core: Core,
        ring: RingCapture,
        relay: PreviewRelay,
        midi: MidiInput | None = None,
        driver_name: str = "cli-wall",
    ) -> None:
        self.core = core
        self.ring = ring
        self.relay = relay
        self.midi = midi
        self.midi_error: str | None = None
        self.driver_name = driver_name
        self.tiles: list[Tile] = []
        self._next_track = 1
        self._recording_track: int | None = None
        self._record_started_at: float | None = None
        self._start_bar: int | None = None
        self._bar = 0
        self._t_prev = 0.0
        self._pending_action: str | None = None  # None | "start" | "stop"
        self._core_lock = asyncio.Lock()
        self._ticker_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self.ring.preview_tcp_port is None:
            raise ValueError("WallDriver requires a RingCapture built with preview_tcp_port set")

        loop = asyncio.get_running_loop()
        loop.set_exception_handler(_quiet_benign_connection_reset)

        await self.relay.start(self.ring.preview_tcp_port)
        self.ring.start()

        if self.midi is not None:
            try:
                self.midi.start(
                    on_bar=lambda: asyncio.create_task(self._on_midi_bar()),
                    on_trigger=lambda: asyncio.create_task(self.toggle()),
                    schedule=functools.partial(loop.call_soon_threadsafe),
                )
                print(f"MIDI connected: {self.midi.port_name}", file=sys.stderr)
            except RuntimeError as exc:
                # A requested MIDI port that isn't there (unplugged,
                # mido missing after all, wrong name) shouldn't take the
                # whole wall down with it - keyboard/browser control still
                # works either way.
                self.midi_error = str(exc)
                self.midi = None
                print(f"MIDI unavailable: {exc}", file=sys.stderr)

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
        if self.midi is not None:
            self.midi.stop()
        await self.relay.stop()
        self.ring.stop()

    async def toggle(self) -> None:
        """Arm a start or stop - applied on the next bar tick, not immediately. See class docstring."""
        async with self._core_lock:
            if self._pending_action is not None:
                return  # already armed for something - a repeat press/trigger is a no-op
            if self._recording_track is None:
                self._pending_action = "start"
            elif time.monotonic() - self._record_started_at >= MIN_RECORD_SECONDS:
                self._pending_action = "stop"

    async def clear(self, track: int) -> None:
        """Remove one completed tile - a plain LDP TrackEvent(state="empty"), not a special case."""
        async with self._core_lock:
            if not any(t.track == track for t in self.tiles):
                return
            await self._dispatch_locked(TrackEvent(track=track, state="empty", pattern=None))
            self.tiles = [t for t in self.tiles if t.track != track]

    async def reset(self) -> None:
        """Clear every completed tile and cancel any pending arm. Leaves an active recording running."""
        async with self._core_lock:
            self._pending_action = None
            for tile in list(self.tiles):
                await self._dispatch_locked(TrackEvent(track=tile.track, state="empty", pattern=None))
            self.tiles = []

    def start_midi_learn(self) -> None:
        if self.midi is not None:
            self.midi.start_learn()

    async def _run_ticker(self) -> None:
        while self._running:
            await asyncio.sleep(TICK_SECONDS)
            if self.midi is not None and self.midi.clock.is_active(time.monotonic()):
                continue  # an external clock is driving bar ticks instead - see _on_midi_bar
            async with self._core_lock:
                await self._apply_pending_and_tick()

    async def _on_midi_bar(self) -> None:
        async with self._core_lock:
            await self._apply_pending_and_tick()

    async def _tick(self, playing: bool) -> None:
        async with self._core_lock:
            await self._tick_locked(playing)

    async def _apply_pending_and_tick(self) -> None:
        action, self._pending_action = self._pending_action, None
        stopped_track: int | None = None
        if action == "start":
            await self._start_recording_locked()
        elif action == "stop":
            stopped_track = self._recording_track
            await self._stop_recording_locked()
        # One tick either closes the capture window just registered above
        # (Core's completion is synchronous) or is just a plain bar tick.
        await self._tick_locked(playing=self._recording_track is not None)
        if stopped_track is not None:
            clip = self.core.session.clips[-1]
            self.tiles.append(
                Tile(
                    track=stopped_track,
                    state="playing",
                    clip_url=f"/clips/{Path(clip.path).name}",
                    duration_seconds=clip.duration_seconds,
                )
            )

    async def _start_recording_locked(self) -> None:
        track = self._next_track
        self._next_track += 1
        self._recording_track = track
        self._start_bar = self._bar
        self._record_started_at = time.monotonic()
        await self._dispatch_locked(TrackEvent(track=track, state="recording", pattern=1))

    async def _stop_recording_locked(self) -> None:
        track = self._recording_track
        await self._dispatch_locked(
            CaptureCueEvent(
                track=track,
                pattern=1,
                cycle_start_bar=self._start_bar,
                bars=self._bar - self._start_bar,
            )
        )
        self._recording_track = None
        self._start_bar = None
        self._record_started_at = None

    async def _tick_locked(self, playing: bool) -> None:
        now = time.monotonic()
        elapsed = max(now - self._t_prev, 1e-6)
        self._bar += 1
        midi_active = self.midi is not None and self.midi.clock.is_active(now)
        bpm = self.midi.clock.bpm if midi_active else 60.0 / elapsed
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
        now = time.monotonic()
        midi_state = None
        if self.midi is not None:
            midi_state = {
                "connected": self.midi.port_name is not None,
                "port_name": self.midi.port_name,
                "clock_active": self.midi.clock.is_active(now),
                "bpm": round(self.midi.clock.bpm, 1),
                "learning": self.midi.learning,
                "trigger": str(self.midi.trigger) if self.midi.trigger is not None else None,
            }
        return {
            "tiles": [
                {
                    "track": t.track,
                    "state": t.state,
                    "clip_url": t.clip_url,
                    "duration_seconds": round(t.duration_seconds, 1),
                }
                for t in self.tiles
            ],
            "recording": self._recording_track is not None,
            "record_seconds": (
                round(now - self._record_started_at, 1) if self._record_started_at is not None else None
            ),
            "armed": self._pending_action,
            "ring": {
                "running": health.is_running,
                "segments": health.segment_count,
                "coverage_seconds": health.ring_coverage_seconds,
            },
            "midi": midi_state,
            "midi_error": self.midi_error,
        }
