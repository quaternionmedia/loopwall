"""LDP/1 wire framing (JSON over WebSocket at /driver, token-auth, single-authority lock).

Built at 0.1.0 build order step 1 (ROADMAP.md), alongside models.py. The
websocket transport itself (token-auth, single-authority lock, FastAPI
`/driver` route) is not built yet - server/app.py stays a stub; the `cli`
driver runs in-process (PLAN.md §3.1 names this a first-class mode, not a
shortcut). What's here is the framing every driver's stream needs
regardless of transport: parsing/dumping one event, and the JSONL append/
read helpers the session log (core/session.py) uses for both raw events
and committed ops - not LDP-specific, but this is where the "log every
driver's stream as timestamped JSONL and replay it" mechanism (PLAN.md
§3.1) actually lives.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from loopwall.ldp.models import LdpEvent

_event_adapter: TypeAdapter[LdpEvent] = TypeAdapter(LdpEvent)


def parse_event(raw: str | bytes | dict) -> LdpEvent:
    if isinstance(raw, dict):
        return _event_adapter.validate_python(raw)
    return _event_adapter.validate_json(raw)


def dump_event(event: LdpEvent) -> dict[str, Any]:
    return event.model_dump(mode="json")


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
