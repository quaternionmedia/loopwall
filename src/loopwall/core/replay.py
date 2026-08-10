"""Replay a session's driver log through a fresh track registry + scheduler.

PLAN.md §3.1: "every driver's LDP stream is logged ... and is replayable."
ROADMAP.md's milestone-1 gate, assertion 4: `loopwall replay <session>`
reproduces an identical committed-op sequence. Deliberately narrower than
re-running the whole session through `Core` - no ring, no ffmpeg, no
`SessionArtifact` write - because determinism here is a claim about
`tracks.py` + `scheduler.py` given the same `track`/`transport` events, the
same subset `tests/property/test_replay_determinism.py` already covers with
synthetic input; this is that same mechanism fed a real log instead of
generated one, so the original footage/ring never needs to still exist.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from loopwall.core.scheduler import CommittedOp, Scheduler
from loopwall.core.tracks import TrackRegistry
from loopwall.ldp.models import LdpEvent
from loopwall.ldp.wire import parse_event, read_jsonl


@dataclass
class ReplayResult:
    replayed_ops: list[CommittedOp]
    logged_ops: list[CommittedOp]

    @property
    def matches(self) -> bool:
        return [op.model_dump() for op in self.replayed_ops] == [op.model_dump() for op in self.logged_ops]


def replay_events(events: Iterable[LdpEvent]) -> list[CommittedOp]:
    """Deterministically re-derive a committed-op sequence from a sequence of
    `track`/`transport` LDP events, through a fresh `TrackRegistry` + `Scheduler` -
    no ring, no ffmpeg, no `SessionArtifact`, no `Core`. This is the driver-agnostic
    primitive PLAN.md §3.1's conformance kit needs: any driver's own event stream,
    replayed through this same function, must produce the same committed-op
    sequence for it to be "honest against one core" - `conformance/test_golden_
    streams.py` is what actually exercises that claim against a real captured
    stream; `replay_session` below is this same mechanism fed a session log
    instead of a bare event list.
    """
    tracks = TrackRegistry()
    scheduler = Scheduler()
    current_bar = 0
    for event in events:
        if event.t == "transport":
            current_bar = event.bar
        elif event.t == "track":
            old, new = tracks.apply(event.track, event.state, event.pattern)
            scheduler.on_track_state_change(event.track, old.state, new.state, current_bar, new.pattern)
    return scheduler.committed


def replay_session(log_path: Path) -> ReplayResult:
    """Re-derive the committed-op sequence from `log_path`'s `track`/`transport`
    events and compare it against the `committed_op` lines the original run logged.
    """
    events: list[LdpEvent] = []
    logged_ops: list[CommittedOp] = []

    for record in read_jsonl(log_path):
        if record["kind"] == "committed_op":
            logged_ops.append(CommittedOp.model_validate(record["data"]))
        else:
            events.append(parse_event(record["data"]))

    return ReplayResult(replayed_ops=replay_events(events), logged_ops=logged_ops)
