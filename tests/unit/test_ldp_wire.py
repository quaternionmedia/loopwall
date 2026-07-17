from pathlib import Path

from loopwall.ldp.models import TrackEvent
from loopwall.ldp.wire import append_jsonl, dump_event, parse_event, read_jsonl


def test_dump_then_parse_roundtrips() -> None:
    event = TrackEvent(track=1, state="recording", pattern=2)
    dumped = dump_event(event)
    parsed = parse_event(dumped)
    assert parsed == event


def test_parse_event_accepts_json_string() -> None:
    parsed = parse_event('{"t": "track", "track": 1, "state": "empty"}')
    assert isinstance(parsed, TrackEvent)
    assert parsed.state == "empty"


def test_append_and_read_jsonl_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    append_jsonl(path, {"a": 1})
    append_jsonl(path, {"b": 2})
    records = list(read_jsonl(path))
    assert records == [{"a": 1}, {"b": 2}]


def test_read_jsonl_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    path.write_text('{"a": 1}\n\n{"b": 2}\n')
    records = list(read_jsonl(path))
    assert records == [{"a": 1}, {"b": 2}]
