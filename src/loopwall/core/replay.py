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

from dataclasses import dataclass
from pathlib import Path

from loopwall.core.scheduler import CommittedOp, Scheduler
from loopwall.core.tracks import TrackRegistry
from loopwall.ldp.wire import parse_event, read_jsonl


@dataclass
class ReplayResult:
    replayed_ops: list[CommittedOp]
    logged_ops: list[CommittedOp]

    @property
    def matches(self) -> bool:
        return [op.model_dump() for op in self.replayed_ops] == [op.model_dump() for op in self.logged_ops]


def replay_session(log_path: Path) -> ReplayResult:
    """Re-derive the committed-op sequence from `log_path`'s `track`/`transport`
    events and compare it against the `committed_op` lines the original run logged.
    """
    tracks = TrackRegistry()
    scheduler = Scheduler()
    current_bar = 0
    logged_ops: list[CommittedOp] = []

    for record in read_jsonl(log_path):
        if record["kind"] == "committed_op":
            logged_ops.append(CommittedOp.model_validate(record["data"]))
            continue
        event = parse_event(record["data"])
        if event.t == "transport":
            current_bar = event.bar
        elif event.t == "track":
            old, new = tracks.apply(event.track, event.state, event.pattern)
            scheduler.on_track_state_change(event.track, old.state, new.state, current_bar, new.pattern)

    return ReplayResult(replayed_ops=scheduler.committed, logged_ops=logged_ops)
