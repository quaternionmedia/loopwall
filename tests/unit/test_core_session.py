import json
from pathlib import Path

from loopwall.core.session import SessionArtifact


def test_construction_creates_dir_and_index(tmp_path: Path) -> None:
    session = SessionArtifact(tmp_path, bpm=120.0, beats_per_bar=4, scenario="smoke")
    assert session.dir.is_dir()
    index = json.loads(session.index_path.read_text())
    assert index["scenario"] == "smoke"
    assert index["bpm"] == 120.0
    assert index["clips"] == []


def test_session_ids_do_not_collide_on_rapid_reruns(tmp_path: Path) -> None:
    sessions = [SessionArtifact(tmp_path, bpm=120.0, beats_per_bar=4) for _ in range(5)]
    assert len({s.session_id for s in sessions}) == 5


def test_log_event_and_committed_op_append_jsonl(tmp_path: Path) -> None:
    session = SessionArtifact(tmp_path, bpm=120.0, beats_per_bar=4)
    session.log_event({"t": "hello"})
    session.log_committed_op({"kind": "add", "track": 1, "bar": 2})
    lines = session.log_path.read_text().strip().splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert first["kind"] == "event"
    assert first["data"] == {"t": "hello"}
    assert second["kind"] == "committed_op"
    assert second["data"]["kind"] == "add"


def test_allocate_clip_path_increments_take_per_track_pattern(tmp_path: Path) -> None:
    session = SessionArtifact(tmp_path, bpm=120.0, beats_per_bar=4)
    path1, take1 = session.allocate_clip_path(track=1, pattern=1)
    assert take1 == 1
    assert path1.name == "track01_p1_take1.mp4"
    # A second allocation before registering the first still returns take 1
    # again - allocate_clip_path only reserves a name, register_clip is
    # what actually advances active_takes.
    path_again, take_again = session.allocate_clip_path(track=1, pattern=1)
    assert (path_again, take_again) == (path1, take1)


def test_register_clip_advances_take_and_updates_index(tmp_path: Path) -> None:
    session = SessionArtifact(tmp_path, bpm=120.0, beats_per_bar=4)
    path, take = session.allocate_clip_path(track=1, pattern=1)
    session.register_clip(
        track=1,
        pattern=1,
        take=take,
        path=path,
        cycle_start_bar=2,
        bars=4,
        duration_seconds=7.9,
        extraction_latency_ms=120.0,
        camera="cam0",
    )
    index = json.loads(session.index_path.read_text())
    assert len(index["clips"]) == 1
    assert index["clips"][0]["duration_seconds"] == 7.9
    assert index["active_takes"]["1:1"] == 1

    next_path, next_take = session.allocate_clip_path(track=1, pattern=1)
    assert next_take == 2
    assert next_path != path
