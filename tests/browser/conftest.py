"""Shared fixtures for the browser-driven `/wall` tests (this directory's own README
explains what these are for and how to set them up).

Two things a real browser needs that `tests/integration/test_wall_app.py`'s
`TestClient`-based version doesn't: an actual listening HTTP socket (`wall_url` launches
`_server_runner.py` as a real subprocess - see that module's own docstring for why a
subprocess, not a background thread) and an actual Chromium instance (the bare
`playwright` package, not the `pytest-playwright` plugin - see the README for why: that
plugin pulls in `python-slugify` -> `text-unidecode`, which is GPL/Artistic-licensed and
outside this project's open-license allowlist, for a convenience feature - naming
screenshot/trace files - these tests don't use).
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from loopwall.proctree import kill_tree

_RUNNER_SCRIPT = Path(__file__).parent / "_server_runner.py"
_REPO_ROOT = Path(__file__).parents[2]  # so create_app's default wall_dir=Path("wall") resolves


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_ready(base_url: str, proc: subprocess.Popen, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"wall server process exited early (code {proc.returncode})")
        try:
            urllib.request.urlopen(f"{base_url}/wall/state", timeout=0.5)
            return
        except Exception as exc:  # any failure here just means "not listening yet"
            last_error = exc
            time.sleep(0.1)
    raise TimeoutError(f"wall server never became ready at {base_url}") from last_error


@pytest.fixture
def wall_url(tmp_path: Path) -> Iterator[str]:
    """A real wall server - `lavfi` camera, real `Core`, real `PreviewRelay` - as a real
    subprocess on a real port. Function-scoped: every test gets its own empty wall and
    its own session directory, the same per-test isolation `test_wall_app.py` uses.
    """
    wall_port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            str(_RUNNER_SCRIPT),
            "--ring-dir",
            str(tmp_path / "ring"),
            "--session-root",
            str(tmp_path / "sessions"),
            "--preview-port",
            str(_free_port()),
            "--wall-port",
            str(wall_port),
        ],
        cwd=str(_REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{wall_port}"
    try:
        _wait_until_ready(base_url, proc)
        yield base_url
    finally:
        kill_tree(proc, timeout=10)


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    """One real Chromium instance shared across this module's tests (each test still gets
    its own `page` below). Deliberately `module`, not `session`, scoped: Playwright's sync
    API runs its own background thread with its own event loop for as long as the
    `sync_playwright()` context is open, and a `session`-scoped fixture would keep that
    thread alive through every later test module too - on this project's Windows dev
    environment that corrupted unrelated `anyio`-based async tests running afterward
    (`RuntimeError: Cannot run the event loop while another loop is running`,
    deterministic and reproducible). `module` scope tears it down as soon as this file's
    tests finish, before anything else runs.

    Skips cleanly, not failed, if Chromium hasn't been installed (`uv run playwright
    install chromium` - see this directory's README), matching every other
    optional-external-tool test in this project (`ffmpeg` via `shutil.which`).
    """
    with sync_playwright() as p:
        try:
            chromium = p.chromium.launch()
        except PlaywrightError as exc:
            pytest.skip(
                f"Playwright's Chromium isn't installed here - run "
                f"`uv run playwright install chromium` (see tests/browser/README.md). ({exc})"
            )
        yield chromium
        chromium.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context()
    page = context.new_page()
    yield page
    context.close()
