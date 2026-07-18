"""FastAPI app serving the wall: one browser window, live preview plus a growing grid of loops.

`/console` (force-live/force-dead masks, PLAN.md §3.5) stays a stub - see
STUBS.md. `/wall` is real now, ahead of its originally planned 0.2.0
target, on your explicit direction to start building toward the grid this
session rather than a throwaway ffmpeg-only stopgap (HANDOFF.md).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from loopwall.drivers.cli.wall import WallDriver


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


def create_app(driver: WallDriver, clips_dir: Path, wall_dir: Path = Path("wall")) -> FastAPI:
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
    app.mount("/clips", StaticFiles(directory=clips_dir), name="clips")

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
        return JSONResponse(driver.state())

    @app.get("/wall/preview.ts")
    async def wall_preview(request: Request) -> StreamingResponse:
        return StreamingResponse(_preview_stream(driver, request), media_type="video/mp2t")

    return app
