"""One-off, run-by-hand converter: a real session's `log.jsonl` -> a golden-stream
JSON file this directory's `test_golden_streams.py` replays in CI.

Not run by CI itself - a golden stream is captured once, by a human, from a real
driver run, and committed like any other test fixture. To add another one (a second
scenario, or hapax's own first real run once that driver exists - PLAN.md §3.1's
"replays golden streams through core, keeping drivers honest against one core"):

    uv run loopwall play scenarios/smoke.yaml --config config.example.yaml
    uv run python conformance/_generate_golden.py <session_dir>/log.jsonl smoke

`expected_committed_ops` is derived with the exact same `core.replay.replay_events`
the test harness itself replays through - this script isn't a second implementation
of the scheduling rule to independently agree with, it's recording what the real
mechanism produced against a real captured stream, for a regression to be checked
against later.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from loopwall.core.replay import replay_events
from loopwall.core.scheduler import CommittedOp
from loopwall.ldp.models import LdpEvent
from loopwall.ldp.wire import dump_event, parse_event, read_jsonl

_GOLDEN_DIR = Path(__file__).parent / "golden"


def build_golden_stream(log_path: Path) -> tuple[list[LdpEvent], list[CommittedOp]]:
    events = [parse_event(record["data"]) for record in read_jsonl(log_path) if record["kind"] == "event"]
    return events, replay_events(events)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_path", type=Path, help="A session's log.jsonl, from a real driver run")
    parser.add_argument("name", help="Output filename stem - written to conformance/golden/<name>.json")
    args = parser.parse_args()

    events, expected_committed_ops = build_golden_stream(args.log_path)

    out_path = _GOLDEN_DIR / f"{args.name}.json"
    out_path.write_text(
        json.dumps(
            {
                "name": args.name,
                "events": [dump_event(e) for e in events],
                "expected_committed_ops": [op.model_dump(mode="json") for op in expected_committed_ops],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"wrote {out_path} ({len(events)} events, {len(expected_committed_ops)} committed ops)")


if __name__ == "__main__":
    main()
