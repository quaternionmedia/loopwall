# tests/browser

Browser-driven end-to-end tests of `/wall` (`server/app.py`, `wall/index.html`) via a
real Chromium instance (the `playwright` package) against a real server running as a real
subprocess — not `TestClient`, and not a mock of the page's JS. Written to double as living
documentation of what a performer actually sees and does at the wall, one behavior per
test with narrating comments; `test_wall_tutorial.py`'s own docstring is the place to
start reading. The manual equivalent is `COOKBOOK.md` recipe 4 — these tests script that
same walkthrough.

`lavfi` only, same as every other browser/lifecycle test in this project — never touches
real capture hardware, not `hardware`-marked.

## One-time setup

```sh
uv sync
uv run playwright install chromium
```

`uv sync` already installs the `playwright` Python package (dev dependency group); the
second command downloads the actual Chromium binary Playwright drives, which isn't part
of the Python package itself. Tests in this directory skip cleanly (not fail) if this
step hasn't been run — see `conftest.py`'s `browser` fixture.

## Running

```sh
uv run pytest tests/browser -v
```

These are picked up by the default `uv run pytest -m "not hardware"` too (`pyproject.toml`'s
`testpaths` covers all of `tests/`), same as any other non-`hardware` test — no separate
marker or invocation needed once Chromium is installed.

## Why the bare `playwright` package, not the `pytest-playwright` plugin

`pytest-playwright` was tried first and reverted: it requires `pytest-base-url`, which
pulls in `python-slugify`, which unconditionally requires `text-unidecode` — dual-licensed
GPL/Artistic, outside this project's open-license allowlist
(`governance/qm/adr/DRAFT-dependency-disposition.md`), for a feature (slugifying test
names for screenshot/trace filenames) these tests don't use. `conftest.py`'s `browser`/
`page` fixtures are a small, self-written replacement — Chromium itself was already an
approved dependency (the eventual kiosk renderer, same ADR), so nothing new is being
bought here beyond the thin pytest glue.

## Why a real subprocess, not `TestClient` (and not a background thread either)

A real browser tab needs something to actually connect to — `conftest.py`'s `wall_url`
fixture launches `_server_runner.py` as a real OS subprocess (real port, real `uvicorn`,
the app's real lifespan). `tests/integration/test_wall_app.py` covers the same lifecycle
faster, via `TestClient`'s in-memory ASGI transport — read that one first if you want the
API-level version without a browser attached.

A background *thread* (same process as pytest, its own `uvicorn`/asyncio event loop) was
tried first and reverted: on this project's Windows dev environment it left the
interpreter's asyncio state corrupted for the rest of the test session once that thread's
loop had run — `RuntimeError: Cannot run the event loop while another loop is running` in
unrelated, later tests (`test_drivers_cli_wall.py`, `test_server_relay.py`), deterministic
and reproducible, even after the thread's own server had shut down cleanly. A real
subprocess sidesteps the question entirely — separate process, separate interpreter,
separate everything — torn down via `loopwall.proctree.kill_tree` (the same mechanism
`RingCapture` itself already relies on for guaranteed teardown) rather than any in-process
signal or flag.

One real bug found chasing this down, fixed in production code, not just worked around in
the test: `WallDriver.stop()` (`src/loopwall/drivers/cli/wall.py`) stopped the preview
relay *before* the ring. The ring's own ffmpeg process is a standing TCP client of the
relay (the tee'd preview stream); `PreviewRelay.stop()`'s `await
self._server.wait_closed()` blocked on that connection actually closing, which only
happens once ffmpeg is killed — measured at ~88 seconds to return (ffmpeg's own
`max_runtime_seconds` cap, 600s by default in production, eventually forces it closed).
Reordering to ring-then-relay dropped that to well under a second. This means a real
`loopwall play` session's Ctrl-C could previously hang for minutes — a real, previously
undiscovered bug, not a hypothetical one.

## One real limitation, named rather than hidden

These tests assert what the DOM and the `/wall/state` JSON say (tile states, labels,
clip URLs, status-line text) — not that the live-preview `<video>` element is actually
decoding real frames via `mpegts.js`. Headless Chromium's video-decode behavior varies
enough across environments (CI containers in particular) that asserting on it directly
risked exactly the kind of environment-dependent flakiness this project's own testing
conventions try to avoid elsewhere. Verifying the live preview actually renders frames is
still a manual, real-hardware check (`COOKBOOK.md` recipe 4).
