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
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from loopwall.capture.composite import composite_grid
from loopwall.capture.extract import probe_duration
from loopwall.capture.ring import RingCapture
from loopwall.core import Core
from loopwall.core.scheduler import Scheduler
from loopwall.core.session import SessionArtifact
from loopwall.core.timeline import Timeline
from loopwall.core.tracks import TrackRegistry
from loopwall.ldp.models import (
    Capabilities,
    CaptureCueEvent,
    HelloEvent,
    LdpEvent,
    TrackEvent,
    TransportEvent,
)
from loopwall.midi import MidiInput, MidiTrigger
from loopwall.monitor.camera import build_input_args
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
    label: str | None = None  # performer-set display name; presentation only, Core never sees it


@dataclass
class CameraSettings:
    """The structured camera selection `RingCapture.input_args` (raw ffmpeg args) can't
    be reversed back into - needed by both `switch_camera` (to rebuild a `RingCapture`
    for a newly picked device with everything else held constant) and `export_config`
    (to describe the current camera in `config.example.yaml`'s own shape).
    """

    camera_type: str
    device: str | None
    width: int
    height: int
    fps: int
    ring_seconds: float
    segment_seconds: float
    ring_dir_base: Path


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
        session_root: Path,
        camera: CameraSettings,
        midi: MidiInput | None = None,
        driver_name: str = "cli-wall",
    ) -> None:
        self.core = core
        self.ring = ring
        self.relay = relay
        self._session_root = session_root
        self._camera = camera
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
        self._switch_generation = 0
        self._render_task: asyncio.Task | None = None
        self._render_status = "idle"  # idle | rendering | done | error
        self._render_error: str | None = None

    @property
    def session_root(self) -> Path:
        """Every session this driver has ever created (past and current) lives directly
        under this directory - `server/app.py`'s `/clips/{session_id}/{filename}` route
        resolves against it, not just `core.session.dir` (the *current* session only).
        """
        return self._session_root

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

        async with self._core_lock:
            await self._send_hello_and_first_tick_locked()
        self._running = True
        self._ticker_task = asyncio.create_task(self._run_ticker())

    async def stop(self) -> None:
        self._running = False
        if self._ticker_task is not None:
            self._ticker_task.cancel()
            self._ticker_task = None
        if self.midi is not None:
            self.midi.stop()
        # Ring before relay, not the other way around: the ring's own ffmpeg process is
        # a standing TCP client of the relay (the tee'd preview stream), so
        # PreviewRelay.stop()'s `await self._server.wait_closed()` blocks on that
        # connection actually closing - which only happens once ffmpeg is killed.
        # Stopping the relay first measured at ~88s to return (ffmpeg's own
        # `max_runtime_seconds` cap - 600s by default - eventually forces it closed);
        # this order returns in well under a second.
        self.ring.stop()
        await self.relay.stop()

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

    async def new_session(self) -> None:
        """Finalize the current session and start a fresh one - same camera/ring/relay
        (never reopened - two processes can't share a camera, and this isn't a second
        process), a brand new `Core` (fresh timeline/tracks/scheduler/session artifact).

        Refuses while a take is actively recording rather than guessing which session
        it belongs to - `reset()` can leave an in-progress recording alone because it
        doesn't touch session identity; this does, so there's no clean "belongs to the
        old session" or "belongs to the new one" answer for a half-finished take.
        """
        async with self._core_lock:
            if self._recording_track is not None:
                raise RuntimeError("stop the current recording before starting a new session")
            self._pending_action = None
            self.core.session.finalize()
            self.core = Core(
                timeline=Timeline(),
                tracks=TrackRegistry(),
                scheduler=Scheduler(),
                session=SessionArtifact(self._session_root, bpm=0.0, beats_per_bar=1),
                ring=self.ring,
                default_camera_id=self.core.default_camera_id,
                extract_fn=self.core.extract_fn,  # preserve any injected extractor, not just the default
            )
            self.tiles = []
            self._next_track = 1
            self._recording_track = None
            self._start_bar = None
            self._record_started_at = None
            self._bar = 0
            self._render_status = "idle"
            self._render_error = None
            await self._send_hello_and_first_tick_locked()

    async def load_session(self, session_id: str) -> None:
        """Resume recording into a past session - not just view it. Same shape as
        `new_session()`, but `SessionArtifact.load()`s an existing directory instead of
        creating a fresh one, and rebuilds `self.tiles` from what that session already
        has.

        Critically, re-dispatches `TrackEvent(state="playing")` for every resumed tile
        before returning: `core/tracks.py`'s `TrackRegistry.get()` defaults an unseen
        track to "empty", so without this seed, a later `clear()` on a resumed tile
        would see "empty"->"empty" (no presence flip, no `remove` op) instead of the
        real "playing"->"empty" transition it needs.
        """
        async with self._core_lock:
            if self._recording_track is not None:
                raise RuntimeError("stop the current recording before loading a session")
            target_dir = self._session_root / session_id
            if not target_dir.is_dir():
                raise ValueError(f"no session {session_id!r} under {self._session_root}")
            self._pending_action = None
            self.core.session.finalize()
            session = SessionArtifact.load(target_dir)
            self.core = Core(
                timeline=Timeline(),
                tracks=TrackRegistry(),
                scheduler=Scheduler(),
                session=session,
                ring=self.ring,
                default_camera_id=self.core.default_camera_id,
                extract_fn=self.core.extract_fn,
            )
            self.tiles = [
                Tile(
                    track=clip.track,
                    state="playing",
                    clip_url=f"/clips/{session.session_id}/{Path(clip.path).name}",
                    duration_seconds=clip.duration_seconds,
                )
                for clip in session.clips
            ]
            self._next_track = max((clip.track for clip in session.clips), default=0) + 1
            self._recording_track = None
            self._start_bar = None
            self._record_started_at = None
            self._bar = 0
            self._render_status = "idle"
            self._render_error = None
            await self._send_hello_and_first_tick_locked()
            for tile in self.tiles:
                await self._dispatch_locked(TrackEvent(track=tile.track, state="playing", pattern=1))

    async def switch_camera(self, camera_type: str, device: str | None) -> None:
        """Live camera swap - same ring/relay wiring `start()` already established
        (`PreviewRelay` needs no changes: it's a plain `asyncio.start_server` that
        already tolerates a fresh inbound connection, not just one ring's lifetime),
        just a different device. Refuses while recording, same reasoning as
        `new_session`: a capture window mid-flight can't cleanly survive the ring
        underneath it changing.

        Uses a fresh ring directory per switch (never the original) so the new
        camera's segment list is never mixed with a previous camera's leftover
        segments.
        """
        async with self._core_lock:
            if self._recording_track is not None:
                raise RuntimeError("stop the current recording before switching cameras")
            old_ring = self.ring
            old_ring.stop()

            self._switch_generation += 1
            input_args = build_input_args(
                camera_type, device, self._camera.width, self._camera.height, self._camera.fps, None
            )
            new_ring = RingCapture(
                ring_dir=self._camera.ring_dir_base / f"switch-{self._switch_generation}",
                input_args=input_args,
                segment_seconds=self._camera.segment_seconds,
                ring_seconds=self._camera.ring_seconds,
                fps=self._camera.fps,
                preview_tcp_port=old_ring.preview_tcp_port,
            )
            new_ring.start()
            self.ring = new_ring
            self.core.ring = new_ring
            self._camera.camera_type = camera_type
            self._camera.device = device

    async def import_clip(self, filename: str, data: bytes) -> None:
        """Add a local video file as a new, permanently looping tile - the same grid a
        captured take lands in, minus the capture itself: straight to "playing", no
        `recording`/`capture_cue` LDP events, since there's no capture window at all.
        """
        async with self._core_lock:
            if self._recording_track is not None:
                raise RuntimeError("stop the current recording before importing a clip")
            track = self._next_track
            self._next_track += 1
            ext = Path(filename).suffix or ".mp4"
            out_path = self.core.session.dir / f"track{track:02d}_p1_import{ext}"
            out_path.write_bytes(data)

            loop = asyncio.get_running_loop()
            try:
                duration = await loop.run_in_executor(None, probe_duration, out_path)
            except (subprocess.CalledProcessError, ValueError) as exc:
                out_path.unlink(missing_ok=True)
                self._next_track -= 1  # give the track number back - nothing was actually added
                raise RuntimeError(f"couldn't read {filename!r} as a video: {exc}") from exc

            await self._dispatch_locked(TrackEvent(track=track, state="playing", pattern=1))
            self.core.session.register_clip(
                track=track,
                pattern=1,
                take=1,
                path=out_path,
                cycle_start_bar=self._bar,
                bars=0,
                duration_seconds=duration,
                extraction_latency_ms=0.0,
                camera="import",
            )
            self.tiles.append(
                Tile(
                    track=track,
                    state="playing",
                    clip_url=f"/clips/{self.core.session.session_id}/{out_path.name}",
                    duration_seconds=duration,
                )
            )

    def select_midi_trigger(self, kind: str, channel: int, number: int) -> None:
        """Pick a trigger directly from `MidiInput.history` instead of "Learn" mode -
        the frontend gets the history from `state()` and posts a chosen entry back
        verbatim, so there's no server-side index to keep in sync with a polling
        client.
        """
        if self.midi is not None:
            self.midi.trigger = MidiTrigger(kind=kind, channel=channel, number=number)

    def export_config(self) -> dict:
        """`config.example.yaml`'s exact shape, describing this run's actual camera/
        ring/session settings - for reproducible scripted replay
        (`loopwall play <scenario> --config <exported>`).
        """
        cam = self._camera
        return {
            "driver": {"type": "cli", "cli": {"bpm": 120}},
            "capture": {
                "cameras": [
                    {
                        "id": "cam0",
                        "type": cam.camera_type,
                        "device": cam.device,
                        "res": f"{cam.width}x{cam.height}",
                        "fps": cam.fps,
                        "offset_ms": 0,
                    }
                ],
                "ring": {"dir": str(cam.ring_dir_base), "seconds": cam.ring_seconds},
                "encoder": "x264",
            },
            "session_root": str(self._session_root),
            "server": {"headless": True},
        }

    async def start_render(self) -> None:
        """Kick off a background composite render (`capture/composite.py`) of every
        currently-visible tile - not blocked by an active recording (independent
        operations, only a CPU-contention cost), but a repeat click while one is
        already running is a no-op, not a second concurrent job.
        """
        async with self._core_lock:
            if self._render_task is not None and not self._render_task.done():
                return
            if not self.tiles:
                raise RuntimeError("nothing to render - the grid is empty")
            clip_paths = [self.core.session.dir / Path(t.clip_url).name for t in self.tiles]
            out_path = self.core.session.dir / "composite.mp4"
            self._render_status = "rendering"
            self._render_error = None
            self._render_task = asyncio.create_task(self._run_render(clip_paths, out_path))

    async def _run_render(self, clip_paths: list[Path], out_path: Path) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, composite_grid, clip_paths, out_path)
        except Exception as exc:  # noqa: BLE001 - any failure here is a real, surfaced render error
            async with self._core_lock:
                self._render_status = "error"
                self._render_error = str(exc)
            return
        async with self._core_lock:
            self._render_status = "done"

    async def set_label(self, track: int, label: str | None) -> None:
        """Performer-set display name for a completed tile - presentation only, no LDP
        event (Core doesn't track grid labels, same as it doesn't track grid position).
        """
        async with self._core_lock:
            for tile in self.tiles:
                if tile.track == track:
                    tile.label = label or None
                    return

    async def move_tile(self, track: int, direction: str) -> None:
        """Swap a completed tile with its left/right neighbor. Clamped at the ends - past
        the boundary is a no-op, same as this class's other unknown/invalid-input calls.
        """
        async with self._core_lock:
            idx = next((i for i, t in enumerate(self.tiles) if t.track == track), None)
            if idx is None:
                return
            new_idx = idx + (-1 if direction == "left" else 1)
            if 0 <= new_idx < len(self.tiles):
                self.tiles[idx], self.tiles[new_idx] = self.tiles[new_idx], self.tiles[idx]

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

    async def _send_hello_and_first_tick_locked(self) -> None:
        """The genesis sequence a fresh `Core` needs before it'll accept anything else -
        shared by `start()` and `new_session()` rather than duplicated between them.
        """
        await self._dispatch_locked(
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
        await self._tick_locked(playing=False)

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
                    # Session-scoped, not just the filename: track/take numbering
                    # restarts at each new_session(), and files from a previous
                    # session are never deleted - a bare filename would collide
                    # with (or shadow) a same-named clip in whatever session is
                    # current when the URL is actually requested.
                    clip_url=f"/clips/{self.core.session.session_id}/{Path(clip.path).name}",
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
                "history": [
                    {"kind": t.kind, "channel": t.channel, "number": t.number, "label": str(t)}
                    for t in self.midi.history
                ],
            }
        render_url = None
        if self._render_status == "done":
            render_url = f"/clips/{self.core.session.session_id}/composite.mp4"
        return {
            "session": {
                "id": self.core.session.session_id,
                "created_at": self.core.session.created_at,
                "dir": str(self.core.session.dir),
            },
            "tiles": [
                {
                    "track": t.track,
                    "state": t.state,
                    "clip_url": t.clip_url,
                    "duration_seconds": round(t.duration_seconds, 1),
                    "label": t.label,
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
            "camera": {"type": self._camera.camera_type, "device": self._camera.device},
            "render": {"status": self._render_status, "url": render_url, "error": self._render_error},
        }
