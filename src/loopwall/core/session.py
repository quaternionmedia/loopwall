"""Session artifact: LDP JSONL log, clips, Pydantic-validated index.json.

Built at 0.1.0 build order step 8 (ROADMAP.md).
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from loopwall.ldp.wire import append_jsonl


class ClipRecord(BaseModel):
    track: int
    pattern: int
    take: int
    bars: int
    path: str
    cycle_start_bar: int
    duration_seconds: float
    extraction_latency_ms: float
    camera: str


class SessionIndex(BaseModel):
    session_id: str
    created_at: str
    scenario: str | None = None
    bpm: float
    beats_per_bar: int
    clips: list[ClipRecord] = Field(default_factory=list)
    active_takes: dict[str, int] = Field(default_factory=dict)  # "track:pattern" -> latest take


class SessionArtifact:
    """One directory per show: log.jsonl (events + committed ops, true time order), clips, index.json.

    The directory is the show's complete, replayable archive (PLAN.md
    §3.3) - clips live directly alongside log.jsonl/index.json, no `clips/`
    subfolder, matching PLAN's wording of the three as siblings.

    Created eagerly at construction - `hello` is the first thing logged,
    and construction happens right before the driver starts, so "on hello"
    and "at construction" are the same moment; deferring adds a bootstrap
    special case for no benefit. `session_id` carries a short uuid suffix
    so rapid re-runs of the same scenario never collide (the milestone-1
    gate's own assertion #6, even though the full gate is deferred here).
    """

    def __init__(self, root: Path, bpm: float, beats_per_bar: int, scenario: str | None = None) -> None:
        self.session_id = f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self.dir = Path(root) / self.session_id
        self.dir.mkdir(parents=True, exist_ok=False)
        self.log_path = self.dir / "log.jsonl"
        self.index_path = self.dir / "index.json"
        self._index = SessionIndex(
            session_id=self.session_id,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            scenario=scenario,
            bpm=bpm,
            beats_per_bar=beats_per_bar,
        )
        self._write_index()

    @classmethod
    def load(cls, dir: Path) -> SessionArtifact:
        """Reopen an existing session directory - `WallDriver.load_session()`'s way of
        resuming recording into a past session. Bypasses `__init__`'s "always create a
        fresh directory" contract (via `__new__`): reads the existing `index.json` back
        into `_index` rather than starting one empty, and points `log_path` at the
        existing `log.jsonl` to keep appending to it, true-time-order, same as any
        other session - not a new file, and not a rewrite of the old one.
        """
        self = cls.__new__(cls)
        self.dir = Path(dir)
        self.session_id = self.dir.name
        self.log_path = self.dir / "log.jsonl"
        self.index_path = self.dir / "index.json"
        self._index = SessionIndex.model_validate_json(self.index_path.read_text())
        return self

    @property
    def clips(self) -> list[ClipRecord]:
        return list(self._index.clips)

    @property
    def created_at(self) -> str:
        return self._index.created_at

    def log_event(self, data: dict[str, Any]) -> None:
        append_jsonl(self.log_path, {"ts": time.time(), "kind": "event", "data": data})

    def log_committed_op(self, data: dict[str, Any]) -> None:
        append_jsonl(self.log_path, {"ts": time.time(), "kind": "committed_op", "data": data})

    def allocate_clip_path(self, track: int, pattern: int) -> tuple[Path, int]:
        """Reserve the next take number and destination path, before extraction runs."""
        take = self._index.active_takes.get(f"{track}:{pattern}", 0) + 1
        path = self.dir / f"track{track:02d}_p{pattern}_take{take}.mp4"
        return path, take

    def register_clip(
        self,
        track: int,
        pattern: int,
        take: int,
        path: Path,
        cycle_start_bar: int,
        bars: int,
        duration_seconds: float,
        extraction_latency_ms: float,
        camera: str,
    ) -> None:
        """Record a clip after extraction succeeds - called after allocate_clip_path()."""
        record = ClipRecord(
            track=track,
            pattern=pattern,
            take=take,
            bars=bars,
            path=str(path),
            cycle_start_bar=cycle_start_bar,
            duration_seconds=duration_seconds,
            extraction_latency_ms=extraction_latency_ms,
            camera=camera,
        )
        self._index.clips.append(record)
        self._index.active_takes[f"{track}:{pattern}"] = take
        self._write_index()

    def _write_index(self) -> None:
        self.index_path.write_text(self._index.model_dump_json(indent=2))

    def finalize(self) -> None:
        self._write_index()
