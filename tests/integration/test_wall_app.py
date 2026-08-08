"""End-to-end wall app lifecycle: real Core, real lavfi ring, real
PreviewRelay (a plain asyncio TCP server, no ffmpeg - see server/relay.py)
- via FastAPI's TestClient, which drives the app's real lifespan
(WallDriver.start()/stop()) without needing a live server socket.

Not `hardware`-marked - lavfi never touches real capture hardware, so
this is safe to run anywhere ffmpeg is installed; skipped, not failed,
when it isn't (matching test_monitor_lifecycle.py's convention).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from loopwall.capture.ring import RingCapture
from loopwall.core import Core
from loopwall.core.scheduler import Scheduler
from loopwall.core.session import SessionArtifact
from loopwall.core.timeline import Timeline
from loopwall.core.tracks import TrackRegistry
from loopwall.drivers.cli.wall import CameraSettings, WallDriver
from loopwall.midi import MidiInput
from loopwall.monitor.camera import build_input_args
from loopwall.server.app import create_app
from loopwall.server.relay import PreviewRelay

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _build(tmp_path: Path, preview_port: int) -> WallDriver:
    ring_dir = tmp_path / "ring"
    input_args = build_input_args("lavfi", None, 320, 240, 10, None)
    ring = RingCapture(
        ring_dir=ring_dir,
        input_args=input_args,
        segment_seconds=1.0,
        ring_seconds=20.0,
        fps=10,
        max_runtime_seconds=25.0,  # hard ceiling regardless of test outcome
        preview_tcp_port=preview_port,
    )
    session_root = tmp_path / "sessions"
    session = SessionArtifact(session_root, bpm=0.0, beats_per_bar=1)
    core = Core(
        timeline=Timeline(),
        tracks=TrackRegistry(),
        scheduler=Scheduler(),
        session=session,
        ring=ring,
        default_camera_id="cam0",
    )
    camera = CameraSettings(
        camera_type="lavfi",
        device=None,
        width=320,
        height=240,
        fps=10,
        ring_seconds=20.0,
        segment_seconds=1.0,
        ring_dir_base=ring_dir,
    )
    return WallDriver(core, ring, PreviewRelay(), session_root=session_root, camera=camera)


def _poll_until(client: TestClient, predicate, timeout: float = 5.0) -> dict:
    """Poll /wall/state until predicate(state) is true - the internal ticker
    (0.2s cadence) applies an armed action asynchronously, on its own
    schedule, not synchronously within the /wall/control response."""
    deadline = time.monotonic() + timeout
    state = client.get("/wall/state").json()
    while not predicate(state):
        if time.monotonic() > deadline:
            raise TimeoutError(f"condition never became true; last state was {state}")
        time.sleep(0.1)
        state = client.get("/wall/state").json()
    return state


def test_wall_page_state_control_and_clip_serving(tmp_path: Path) -> None:
    driver = _build(tmp_path, preview_port=17401)
    app = create_app(driver, wall_dir=Path("wall"))

    with TestClient(app) as client:
        page = client.get("/wall")
        assert page.status_code == 200
        assert "loopwall" in page.text
        assert "/wall/preview.ts" in page.text

        script = client.get("/wall/mpegts.js")
        assert script.status_code == 200
        assert "mpegts" in script.text

        state = client.get("/wall/state").json()
        assert state["tiles"] == []
        assert state["recording"] is False

        state = client.post("/wall/control", json={"action": "toggle"}).json()
        assert state["armed"] == "start"  # toggle only arms - the internal ticker applies it

        _poll_until(client, lambda s: s["recording"] is True)

        time.sleep(1.1)  # clear WallDriver's MIN_RECORD_SECONDS

        state = client.post("/wall/control", json={"action": "toggle"}).json()
        assert state["armed"] == "stop"

        state = _poll_until(client, lambda s: len(s["tiles"]) == 1)
        assert state["recording"] is False
        clip_url = state["tiles"][0]["clip_url"]

        clip_resp = client.get(clip_url)
        assert clip_resp.status_code == 200
        assert len(clip_resp.content) > 0

        state = client.post("/wall/control", json={"action": "reset"}).json()
        assert state["tiles"] == []
        assert state["midi"] is None  # no --midi requested for this driver


def _record_one_take(client: TestClient, expected_tile_count: int) -> dict:
    """`expected_tile_count`, not just "any tile" - a second take's own poll would
    otherwise be satisfied immediately by the *first* take's tile still being there,
    returning before the second one has actually landed.
    """
    client.post("/wall/control", json={"action": "toggle"})
    _poll_until(client, lambda s: s["recording"] is True)
    time.sleep(1.1)  # clear WallDriver's MIN_RECORD_SECONDS
    client.post("/wall/control", json={"action": "toggle"})
    return _poll_until(client, lambda s: s["recording"] is False and len(s["tiles"]) == expected_tile_count)


def test_session_info_new_session_and_clip_serving_after(tmp_path: Path) -> None:
    driver = _build(tmp_path, preview_port=17402)
    app = create_app(driver, wall_dir=Path("wall"))

    with TestClient(app) as client:
        first = client.get("/wall/state").json()
        first_session = first["session"]
        assert first_session["id"] and first_session["dir"] and first_session["created_at"]

        state = _record_one_take(client, expected_tile_count=1)
        clip_url = state["tiles"][0]["clip_url"]
        assert first_session["id"] in clip_url  # session-scoped, not just the filename
        assert client.get(clip_url).status_code == 200

        # Blocked while a take is actively recording - refuses rather than guessing
        # which session a half-finished clip belongs to.
        client.post("/wall/control", json={"action": "toggle"})  # arm start
        _poll_until(client, lambda s: s["recording"] is True)
        resp = client.post("/wall/control", json={"action": "new_session"})
        assert resp.status_code == 409
        assert "recording" in resp.json()["detail"]

        time.sleep(1.1)
        client.post("/wall/control", json={"action": "toggle"})  # arm stop
        _poll_until(client, lambda s: s["recording"] is False)

        resp = client.post("/wall/control", json={"action": "new_session"})
        assert resp.status_code == 200
        state = resp.json()
        assert state["tiles"] == []
        assert state["session"]["id"] != first_session["id"]
        assert state["session"]["dir"] != first_session["dir"]

        # The old session's clip is gone from the grid but its file is still on disk,
        # reachable via its own session-scoped URL - new_session() never deletes
        # anything, only stops pointing the grid at it.
        assert client.get(clip_url).status_code == 200

        # A clip recorded *after* new_session lands in the new session's directory,
        # under the new session's id - proving /clips resolves per-session, not
        # against whichever one create_app happened to be built with.
        state = _record_one_take(client, expected_tile_count=1)
        new_clip_url = state["tiles"][0]["clip_url"]
        assert new_clip_url != clip_url
        assert state["session"]["id"] in new_clip_url
        assert client.get(new_clip_url).status_code == 200


def test_clip_route_rejects_path_traversal_and_missing_files(tmp_path: Path) -> None:
    driver = _build(tmp_path, preview_port=17403)
    app = create_app(driver, wall_dir=Path("wall"))

    with TestClient(app) as client:
        state = client.get("/wall/state").json()
        real_session_id = state["session"]["id"]

        # A plain, unencoded ".." never reaches the server as a literal path segment
        # in the first place - httpx (TestClient's own HTTP client) normalizes it
        # client-side per RFC 3986 before the request is even sent, same as a real
        # browser would. Percent-encoding is what actually exercises the route
        # handler's own guard rather than being resolved away before it ever runs.
        assert client.get("/clips/%2e%2e/passwd").status_code == 404
        assert client.get(f"/clips/{real_session_id}/%2e%2e").status_code == 404
        assert client.get("/clips/nonexistent-session/track01_p1_take1.mp4").status_code == 404
        assert client.get(f"/clips/{real_session_id}/nonexistent.mp4").status_code == 404


def test_label_and_move_actions(tmp_path: Path) -> None:
    driver = _build(tmp_path, preview_port=17404)
    app = create_app(driver, wall_dir=Path("wall"))

    with TestClient(app) as client:
        _record_one_take(client, expected_tile_count=1)
        state = _record_one_take(client, expected_tile_count=2)
        track1, track2 = (t["track"] for t in state["tiles"])
        assert [t["label"] for t in state["tiles"]] == [None, None]

        state = client.post(
            "/wall/control", json={"action": "label", "track": track1, "text": "intro loop"}
        ).json()
        labels = {t["track"]: t["label"] for t in state["tiles"]}
        assert labels[track1] == "intro loop"
        assert labels[track2] is None

        # Clearing a label (empty text) reverts to no label, not an empty string.
        state = client.post("/wall/control", json={"action": "label", "track": track1, "text": ""}).json()
        assert {t["track"]: t["label"] for t in state["tiles"]}[track1] is None

        # Move track2 left, swapping the two tiles' grid order.
        state = client.post(
            "/wall/control", json={"action": "move", "track": track2, "direction": "left"}
        ).json()
        assert [t["track"] for t in state["tiles"]] == [track2, track1]

        # Already leftmost - moving left again is a no-op, not an error.
        state = client.post(
            "/wall/control", json={"action": "move", "track": track2, "direction": "left"}
        ).json()
        assert [t["track"] for t in state["tiles"]] == [track2, track1]

    assert not driver.ring.is_running


def test_camera_list_endpoint_returns_a_list(tmp_path: Path) -> None:
    """Content is host-hardware-dependent (real devices vary machine to machine, and
    CI has none at all) - only the endpoint's own shape is asserted."""
    driver = _build(tmp_path, preview_port=17405)
    app = create_app(driver, wall_dir=Path("wall"))

    with TestClient(app) as client:
        resp = client.get("/wall/cameras")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


def test_switch_camera_swaps_the_ring_and_the_wall_keeps_working(tmp_path: Path) -> None:
    driver = _build(tmp_path, preview_port=17406)
    app = create_app(driver, wall_dir=Path("wall"))

    with TestClient(app) as client:
        first_ring = driver.ring
        resp = client.post(
            "/wall/control", json={"action": "switch_camera", "camera_type": "lavfi", "device": None}
        )
        assert resp.status_code == 200
        assert driver.ring is not first_ring
        assert not first_ring.is_running
        assert driver.ring.is_running

        # A recording still works after the switch - proves the new ring is genuinely
        # wired into both WallDriver and Core (Core.ring is reassigned too).
        state = _record_one_take(client, expected_tile_count=1)
        assert client.get(state["tiles"][0]["clip_url"]).status_code == 200


def test_switch_camera_refuses_while_recording_via_http(tmp_path: Path) -> None:
    driver = _build(tmp_path, preview_port=17407)
    app = create_app(driver, wall_dir=Path("wall"))

    with TestClient(app) as client:
        client.post("/wall/control", json={"action": "toggle"})
        _poll_until(client, lambda s: s["recording"] is True)
        resp = client.post(
            "/wall/control", json={"action": "switch_camera", "camera_type": "lavfi", "device": None}
        )
        assert resp.status_code == 409


def test_midi_bind_camera_trigger_round_trips_through_state(tmp_path: Path) -> None:
    """No real MIDI port needed - `MidiInput` attached directly, never `.start()`ed,
    same as this driver's own `select_midi_trigger` needs nothing beyond an
    attached `MidiInput` to be exercised for real over HTTP.
    """
    driver = _build(tmp_path, preview_port=17408)
    driver.midi = MidiInput(beats_per_bar=1)
    app = create_app(driver, wall_dir=Path("wall"))

    with TestClient(app) as client:
        resp = client.post(
            "/wall/control",
            json={
                "action": "midi_bind_camera_trigger",
                "kind": "note_on",
                "channel": 0,
                "number": 36,
                "camera_type": "lavfi",
                "device": "sim0",
            },
        )
        assert resp.status_code == 200

        state = client.get("/wall/state").json()
        assert state["midi"]["camera_triggers"] == [
            {
                "kind": "note_on",
                "channel": 0,
                "number": 36,
                "label": "note 36 ch1",
                "camera_type": "lavfi",
                "device": "sim0",
            }
        ]


def _make_tiny_video(path: Path, seconds: float = 1.0) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=5",
            "-t",
            str(seconds),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            str(path),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )


def test_import_endpoint_adds_a_tile(tmp_path: Path) -> None:
    driver = _build(tmp_path, preview_port=17408)
    app = create_app(driver, wall_dir=Path("wall"))
    tiny = tmp_path / "tiny.mp4"
    _make_tiny_video(tiny)

    with TestClient(app) as client:
        with tiny.open("rb") as f:
            resp = client.post("/wall/import", files={"file": ("clip.mp4", f, "video/mp4")})
        assert resp.status_code == 200
        state = resp.json()
        assert len(state["tiles"]) == 1
        assert state["tiles"][0]["clip_url"].endswith(".mp4")
        assert client.get(state["tiles"][0]["clip_url"]).status_code == 200


def test_import_endpoint_rejects_unsupported_extension(tmp_path: Path) -> None:
    driver = _build(tmp_path, preview_port=17409)
    app = create_app(driver, wall_dir=Path("wall"))

    with TestClient(app) as client:
        resp = client.post("/wall/import", files={"file": ("notes.txt", b"hello", "text/plain")})
        assert resp.status_code == 400


def test_sessions_endpoint_and_load_session_round_trip(tmp_path: Path) -> None:
    driver = _build(tmp_path, preview_port=17410)
    app = create_app(driver, wall_dir=Path("wall"))

    with TestClient(app) as client:
        first_id = client.get("/wall/state").json()["session"]["id"]
        _record_one_take(client, expected_tile_count=1)

        resp = client.post("/wall/control", json={"action": "new_session"})
        assert resp.status_code == 200
        second_id = resp.json()["session"]["id"]

        sessions = client.get("/wall/sessions").json()
        ids = [s["id"] for s in sessions]
        assert first_id in ids
        assert second_id in ids
        first_entry = next(s for s in sessions if s["id"] == first_id)
        assert first_entry["clip_count"] == 1

        resp = client.post("/wall/control", json={"action": "load_session", "session_id": first_id})
        assert resp.status_code == 200
        state = resp.json()
        assert state["session"]["id"] == first_id
        assert len(state["tiles"]) == 1

        resp = client.post("/wall/control", json={"action": "load_session", "session_id": "does-not-exist"})
        assert resp.status_code == 404


def test_config_export_downloads_yaml(tmp_path: Path) -> None:
    driver = _build(tmp_path, preview_port=17411)
    app = create_app(driver, wall_dir=Path("wall"))

    with TestClient(app) as client:
        resp = client.get("/wall/config.yaml")
        assert resp.status_code == 200
        assert resp.headers["content-disposition"] == "attachment; filename=config.yaml"
        config = yaml.safe_load(resp.text)
        assert config["driver"]["type"] == "cli"
        assert config["capture"]["cameras"][0]["type"] == "lavfi"


def test_render_composite_end_to_end(tmp_path: Path) -> None:
    driver = _build(tmp_path, preview_port=17412)
    app = create_app(driver, wall_dir=Path("wall"))

    with TestClient(app) as client:
        resp = client.post("/wall/control", json={"action": "render"})
        assert resp.status_code == 409  # nothing to render yet

        _record_one_take(client, expected_tile_count=1)
        _record_one_take(client, expected_tile_count=2)

        resp = client.post("/wall/control", json={"action": "render"})
        assert resp.status_code == 200
        assert resp.json()["render"]["status"] in ("rendering", "done")  # a fast render may finish already

        state = _poll_until(client, lambda s: s["render"]["status"] in ("done", "error"), timeout=30.0)
        assert state["render"]["status"] == "done", state["render"]
        assert client.get(state["render"]["url"]).status_code == 200
