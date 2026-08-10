"""Replays every `conformance/golden/*.json` stream through `core.replay.replay_events`
and asserts the resulting committed-op sequence matches what's recorded - PLAN.md
§3.1's "replays golden streams through core, keeping drivers honest against one
core," for real, not as a stand-in (see this directory's own README).

Auto-discovered by CI's "Conformance replay" step (`.github/workflows/ci.yml`),
which runs `pytest conformance -q` against whatever `test_*.py` it finds here - no
CI configuration change needed to pick this file up or any golden stream added
after it. To add another golden stream, see `_generate_golden.py`'s own docstring.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopwall.core.replay import replay_events
from loopwall.core.scheduler import CommittedOp
from loopwall.ldp.wire import parse_event

_GOLDEN_DIR = Path(__file__).parent / "golden"
_GOLDEN_FILES = sorted(_GOLDEN_DIR.glob("*.json"))


@pytest.mark.parametrize("golden_path", _GOLDEN_FILES, ids=lambda p: p.stem)
def test_golden_stream_replays_to_the_recorded_committed_ops(golden_path: Path) -> None:
    golden = json.loads(golden_path.read_text())
    events = [parse_event(e) for e in golden["events"]]
    expected = [CommittedOp.model_validate(op) for op in golden["expected_committed_ops"]]

    replayed = replay_events(events)

    assert [op.model_dump() for op in replayed] == [op.model_dump() for op in expected]


def test_at_least_one_golden_stream_exists() -> None:
    """A guard against this directory silently going empty (parametrize with zero
    cases collects and passes trivially) - the one failure mode a parametrized
    test can't catch on its own.
    """
    assert _GOLDEN_FILES, f"no golden streams found in {_GOLDEN_DIR}"
