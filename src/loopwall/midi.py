"""External MIDI clock/transport + an assignable start/stop trigger for the wall.

Optional: `mido`/`python-rtmidi` live in the existing `hapax` extra
(pyproject.toml), not a base dependency - `python-rtmidi` builds a native
extension from source on platforms without a prebuilt wheel and needs a C
toolchain (confirmed the hard way on this dev machine: no compiler, no
build). Soft-imported here so the wall runs exactly as before - keyboard/
browser control only - when it isn't installed, rather than failing to
start. `is_available()` is what callers check first.

`ClockTracker` and `MidiTrigger` are pure, no I/O - fully unit-testable
without `mido` installed at all, or real hardware. `MidiInput` is the
thin glue around a real `mido` port; it's what actually needs hardware
(or at minimum a working `mido` install) to exercise for real.
"""

from __future__ import annotations

import sys
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

try:
    import mido
except ImportError:
    mido = None  # type: ignore[assignment]


def is_available() -> bool:
    return mido is not None


@dataclass
class MidiTrigger:
    """A learned start/stop binding: one note or one CC, on one channel."""

    kind: str  # "note_on" | "control_change"
    channel: int
    number: int  # note number (kind="note_on") or controller number (kind="control_change")

    def matches(self, msg: object) -> bool:
        if getattr(msg, "type", None) != self.kind:
            return False
        if getattr(msg, "channel", None) != self.channel:
            return False
        if self.kind == "note_on":
            return getattr(msg, "note", None) == self.number
        return getattr(msg, "control", None) == self.number

    def __str__(self) -> str:
        label = "note" if self.kind == "note_on" else "cc"
        return f"{label} {self.number} ch{self.channel + 1}"


class ClockTracker:
    """MIDI clock pulses (0xF8, 24 per quarter note) -> bpm + bar-boundary detection.

    Pure logic, no I/O, no threading - the MIDI input callback (a
    different thread, see MidiInput) just feeds it timestamps.
    """

    PULSES_PER_BEAT = 24

    def __init__(self, beats_per_bar: int = 4) -> None:
        self.beats_per_bar = beats_per_bar
        self.bpm = 120.0
        self.running = False
        self.last_pulse_at: float | None = None
        self._intervals: deque[float] = deque(maxlen=self.PULSES_PER_BEAT)
        self._pulse_in_beat = 0
        self._beat_in_bar = 0

    def start(self) -> None:
        """MIDI Start (0xFA) or Continue (0xFB) - realign to bar 1, beat 1."""
        self.running = True
        self._pulse_in_beat = 0
        self._beat_in_bar = 0

    def stop(self) -> None:
        """MIDI Stop (0xFC) - clock pulses may keep arriving while stopped; ignored until start()."""
        self.running = False

    def pulse(self, now: float) -> bool:
        """Record one clock pulse (0xF8) at monotonic time `now`. Returns True if it completed a bar."""
        if not self.running:
            return False
        if self.last_pulse_at is not None:
            interval = now - self.last_pulse_at
            if 0 < interval < 1.0:  # ignore bogus/huge gaps, e.g. resuming after a long stop
                self._intervals.append(interval)
        self.last_pulse_at = now
        if self._intervals:
            avg = sum(self._intervals) / len(self._intervals)
            if avg > 0:
                self.bpm = 60.0 / (avg * self.PULSES_PER_BEAT)

        self._pulse_in_beat += 1
        if self._pulse_in_beat < self.PULSES_PER_BEAT:
            return False
        self._pulse_in_beat = 0
        self._beat_in_bar += 1
        if self._beat_in_bar < self.beats_per_bar:
            return False
        self._beat_in_bar = 0
        return True

    def is_active(self, now: float, timeout: float = 2.0) -> bool:
        """True if a real external clock has been ticking recently - the wall's own internal
        ticker steps aside while this is true (WallDriver._run_ticker)."""
        return self.running and self.last_pulse_at is not None and (now - self.last_pulse_at) < timeout


class MidiInput:
    """Owns one `mido` input port, bridging its callback thread into the asyncio loop.

    `mido`'s callback fires on a thread `rtmidi` owns internally, never
    the asyncio loop thread - `on_bar`/`on_trigger` are plain sync
    callables the caller schedules onto its own loop (e.g. via
    `loop.call_soon_threadsafe`), not coroutines called directly here.
    """

    def __init__(self, beats_per_bar: int = 4, port_name: str | None = None) -> None:
        # No mido check here - only start() actually touches mido, which
        # keeps handle_message()'s logic testable (fake message objects,
        # no real port) even where mido itself can't be installed.
        self.clock = ClockTracker(beats_per_bar)
        self.trigger: MidiTrigger | None = None
        self.learning = False
        self.requested_port_name = port_name
        self._on_bar: Callable[[], None] | None = None
        self._on_trigger: Callable[[], None] | None = None
        self._schedule: Callable[[Callable[[], None]], None] | None = None
        self._port: object | None = None

    @property
    def port_name(self) -> str | None:
        return getattr(self._port, "name", None) if self._port is not None else None

    @staticmethod
    def list_input_names() -> list[str]:
        if mido is None:
            return []
        return mido.get_input_names()

    def start(
        self,
        on_bar: Callable[[], None],
        on_trigger: Callable[[], None],
        schedule: Callable[[Callable[[], None]], None],
        port_name: str | None = None,
    ) -> None:
        """Open the input port and start listening. `schedule` bridges this class's
        callback thread onto whatever loop `on_bar`/`on_trigger` need to run on -
        typically `functools.partial(loop.call_soon_threadsafe)`."""
        if mido is None:
            raise RuntimeError("mido not installed - MIDI unavailable (uv sync --extra hapax)")
        self._on_bar = on_bar
        self._on_trigger = on_trigger
        self._schedule = schedule
        name = port_name or self.requested_port_name or next(iter(self.list_input_names()), None)
        if name is None:
            raise RuntimeError("no MIDI input ports found")
        self._port = mido.open_input(name, callback=self._on_message)

    def stop(self) -> None:
        if self._port is not None:
            self._port.close()
            self._port = None

    def start_learn(self) -> None:
        self.learning = True

    def handle_message(self, msg: object) -> None:
        """The actual message-handling logic, factored out of the real `mido` callback
        so it can be exercised with plain fake messages in tests, without a real port."""
        msg_type = getattr(msg, "type", None)
        if msg_type == "clock":
            if self.clock.pulse(time.monotonic()) and self._on_bar is not None and self._schedule is not None:
                self._schedule(self._on_bar)
            return
        if msg_type in ("start", "continue"):
            self.clock.start()
            return
        if msg_type == "stop":
            self.clock.stop()
            return
        if msg_type == "note_on" and getattr(msg, "velocity", 1) == 0:
            return  # many controllers send note_on velocity=0 in place of note_off
        if msg_type in ("note_on", "control_change"):
            if self.learning:
                self.trigger = MidiTrigger(
                    kind=msg_type,
                    channel=msg.channel,
                    number=msg.note if msg_type == "note_on" else msg.control,
                )
                self.learning = False
                return
            if (
                self.trigger is not None
                and self.trigger.matches(msg)
                and self._on_trigger is not None
                and self._schedule is not None
            ):
                self._schedule(self._on_trigger)

    def _on_message(self, msg: object) -> None:
        # This runs on rtmidi's own callback thread - handle_message() itself must
        # stay free of asyncio calls; only `self._schedule` may cross that boundary.
        # An uncaught exception here would otherwise vanish silently (this thread
        # is owned by the C extension, not asyncio or the interpreter's main
        # thread) and the port would look "connected but unresponsive" with no
        # clue why - print it instead of losing it.
        try:
            self.handle_message(msg)
        except Exception as exc:  # noqa: BLE001 - last-resort visibility on a foreign thread
            print(f"MIDI callback error on {msg!r}: {exc!r}", file=sys.stderr)
