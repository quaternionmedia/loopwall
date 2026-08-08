"""FastAPI app serving the wall: one browser window, live preview plus a growing grid of loops.

`/console` (force-live/force-dead masks, PLAN.md §3.5) stays a stub - see
STUBS.md. `/wall` is real now, ahead of its originally planned 0.2.0
target, on your explicit direction to start building toward the grid this
session rather than a throwaway ffmpeg-only stopgap (HANDOFF.md).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

from loopwall.core.session import SessionIndex
from loopwall.drivers.cli.wall import WallDriver
from loopwall.monitor.camera import list_camera_devices

_IMPORT_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}


async def _preview_stream(driver: WallDriver, request: Request) -> AsyncIterator[bytes]:
    """Raw MPEG-TS bytes, straight from `PreviewRelay` - no transcoding in this process at all.

    Checks for client disconnect on every chunk as a backstop: a
    generator with nothing watching for that would run forever once
    started, tying up an event-loop slot for no one.
    """
    async for chunk in driver.relay.subscribe():
        if await request.is_disconnected():
            return
        yield chunk


def create_app(driver: WallDriver, wall_dir: Path = Path("wall")) -> FastAPI:
    """Build the wall's FastAPI app around an already-constructed `driver`.

    A factory, not a module-level `app`, since `driver` (and the ring,
    camera, and session behind it) is only known once `loopwall play` has
    parsed its own config/camera flags - there's nothing meaningful to
    build at import time.
    """

    async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
        await driver.start()
        try:
            yield
        finally:
            await driver.stop()

    app = FastAPI(lifespan=_lifespan)

    @app.get("/clips/{session_id}/{filename}")
    async def get_clip(session_id: str, filename: str) -> FileResponse:
        """Resolved against `driver.session_root` at request time, not a fixed directory
        and not just the *current* session - `new_session()` (`drivers/cli/wall.py`)
        replaces `core.session` mid-process, and track/take numbering restarts each
        time, so a clip is only unambiguous once its session id is part of the URL.
        Past sessions' clips stay reachable this way too: `new_session()` never
        deletes anything, only stops pointing the grid at it.
        """
        for segment in (session_id, filename):
            if "/" in segment or "\\" in segment or segment in (".", ".."):
                raise HTTPException(status_code=404)
        path = driver.session_root / session_id / filename
        if not path.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(path)

    @app.get("/wall", response_class=HTMLResponse)
    async def wall_page() -> str:
        return (wall_dir / "index.html").read_text(encoding="utf-8")

    @app.get("/wall/mpegts.js")
    async def mpegts_js() -> Response:
        return Response(
            (wall_dir / "mpegts.js").read_text(encoding="utf-8"), media_type="application/javascript"
        )

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/wall")

    @app.get("/wall/state")
    async def wall_state() -> JSONResponse:
        return JSONResponse(driver.state())

    @app.post("/wall/control")
    async def wall_control(body: dict) -> JSONResponse:
        action = body.get("action")
        if action == "toggle":
            await driver.toggle()
        elif action == "clear":
            await driver.clear(int(body["track"]))
        elif action == "reset":
            await driver.reset()
        elif action == "midi_learn":
            driver.start_midi_learn()
        elif action == "new_session":
            try:
                await driver.new_session()
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        elif action == "label":
            await driver.set_label(int(body["track"]), body.get("text"))
        elif action == "move":
            await driver.move_tile(int(body["track"]), body["direction"])
        elif action == "load_session":
            try:
                await driver.load_session(body["session_id"])
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        elif action == "switch_camera":
            try:
                await driver.switch_camera(body["camera_type"], body.get("device"))
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        elif action == "midi_select_trigger":
            driver.select_midi_trigger(body["kind"], int(body["channel"]), int(body["number"]))
        elif action == "render":
            try:
                await driver.start_render()
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(driver.state())

    @app.get("/wall/preview.ts")
    async def wall_preview(request: Request) -> StreamingResponse:
        return StreamingResponse(_preview_stream(driver, request), media_type="video/mp2t")

    @app.get("/wall/cameras")
    async def wall_cameras() -> JSONResponse:
        """On-demand, not part of the 200ms poll - only matters while the camera
        picker is actually open."""
        loop = asyncio.get_running_loop()
        devices = await loop.run_in_executor(None, list_camera_devices)
        return JSONResponse([{"camera_type": t, "device": d} for t, d in devices])

    @app.get("/wall/sessions")
    async def wall_sessions() -> JSONResponse:
        """Past sessions under this driver's session_root, newest first - on-demand,
        same reasoning as /wall/cameras."""
        sessions = []
        if driver.session_root.is_dir():
            for entry in driver.session_root.iterdir():
                index_path = entry / "index.json"
                if not index_path.is_file():
                    continue
                try:
                    index = SessionIndex.model_validate_json(index_path.read_text())
                except (ValueError, OSError):
                    continue  # malformed/unreadable - skip rather than fail the whole list
                sessions.append(
                    {"id": index.session_id, "created_at": index.created_at, "clip_count": len(index.clips)}
                )
        sessions.sort(key=lambda s: s["created_at"], reverse=True)
        return JSONResponse(sessions)

    @app.post("/wall/import")
    async def wall_import(file: UploadFile) -> JSONResponse:
        name = file.filename or "clip.mp4"
        if Path(name).suffix.lower() not in _IMPORT_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"unsupported file type: {name!r}")
        data = await file.read()
        try:
            await driver.import_clip(name, data)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(driver.state())

    @app.get("/wall/config.yaml")
    async def wall_config() -> Response:
        text = yaml.safe_dump(driver.export_config(), sort_keys=False)
        return Response(
            text,
            media_type="text/yaml",
            headers={"Content-Disposition": "attachment; filename=config.yaml"},
        )

    return app
