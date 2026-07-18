"""Unit tests for core.replay - a driver log's track/transport events back
through a fresh scheduler, independent of Core/ffmpeg/SessionArtifact.
"""

from __future__ import annotations

import json
from pathlib import Path

from loopwall.core.replay import replay_session
from loopwall.core.scheduler import CommittedOp
from loopwall.core.session import SessionArtifact
from loopwall.ldp.models import HelloEvent, TrackEvent, TransportEvent
from loopwall.ldp.wire import dump_event


def _write_log(session: SessionArtifact, events: list, committed_ops: list[CommittedOp]) -> None:
    for event in events:
        session.log_event(dump_event(event))
    for op in committed_ops:
        session.log_committed_op(op.model_dump())


def test_replay_matches_a_log_produced_by_a_real_session(tmp_path: Path) -> None:
    session = SessionArtifact(tmp_path, bpm=120.0, beats_per_bar=4, scenario="unit")
    session.log_event(dump_event(HelloEvent(driver="test", caps=_caps())))
    session.log_event(
        dump_event(TransportEvent(playing=False, bpm=120, bar=0, beat=1, phase=0, beats_per_bar=4))
    )
    session.log_event(dump_event(TrackEvent(track=1, state="recording", pattern=1)))
    op = CommittedOp(kind="add", track=1, bar=0, pattern=1)
    session.log_committed_op(op.model_dump())
    session.log_event(
        dump_event(TransportEvent(playing=True, bpm=120, bar=1, beat=1, phase=0, beats_per_bar=4))
    )
    session.log_event(
        dump_event(TrackEvent(track=1, state="playing", pattern=1))
    )  # presence-preserving, no op

    result = replay_session(session.log_path)

    assert result.matches
    assert [o.kind for o in result.replayed_ops] == ["add"]
    assert [o.bar for o in result.replayed_ops] == [0]


def test_replay_detects_a_log_that_was_hand_tampered(tmp_path: Path) -> None:
    session = SessionArtifact(tmp_path, bpm=120.0, beats_per_bar=4, scenario="unit")
    session.log_event(
        dump_event(TransportEvent(playing=False, bpm=120, bar=0, beat=1, phase=0, beats_per_bar=4))
    )
    session.log_event(dump_event(TrackEvent(track=1, state="recording", pattern=1)))
    session.log_committed_op(CommittedOp(kind="add", track=1, bar=0, pattern=1).model_dump())

    # Tamper: rewrite the logged committed_op to claim a bar that never happened.
    lines = session.log_path.read_text().splitlines()
    tampered = []
    for line in lines:
        record = json.loads(line)
        if record["kind"] == "committed_op":
            record["data"]["bar"] = 99
        tampered.append(json.dumps(record))
    session.log_path.write_text("\n".join(tampered) + "\n")

    result = replay_session(session.log_path)

    assert not result.matches
    assert result.replayed_ops[0].bar == 0
    assert result.logged_ops[0].bar == 99


def _caps():
    from loopwall.ldp.models import Capabilities

    return Capabilities(
        clock="internal", explicit_state=True, capture_cues=True, media_binding=False, style="event"
    )
