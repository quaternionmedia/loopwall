"""A guided tour of `/wall` through a real browser (Chromium, via `playwright`) - the
browser-driven counterpart to `tests/integration/test_wall_app.py`'s `TestClient`
version, and to `COOKBOOK.md` recipe 4's manual walkthrough. Written to double as living
documentation: each test narrates one piece of what a performer actually sees and does at
`/wall`, not just what the JSON API returns - see this directory's own README.

Not `hardware`-marked - `lavfi` never touches real capture hardware, so this is safe to
run anywhere `ffmpeg` and a Playwright-installed Chromium are both present; skips
cleanly, not failed, when either is missing (matching `test_monitor_lifecycle.py`'s and
`test_wall_app.py`'s own convention for `ffmpeg`; see `conftest.py`'s `browser` fixture
for Chromium).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")

# WallDriver.MIN_RECORD_SECONDS (src/loopwall/drivers/cli/wall.py) - a stop pressed
# before this much real time has passed since the start is ignored, so every take in
# this file waits a little longer than it before arming a stop.
_MIN_RECORD_MS = 1200

# Real ffmpeg extraction (a fresh x264 encode per take, not a mock) plus the internal
# ticker's own 0.2s cadence - generous but bounded, matching test_wall_app.py's own
# poll-with-timeout approach rather than a fixed sleep.
_EXTRACTION_TIMEOUT_MS = 15_000


def _record_one_take(page: Page, expected_tile_count: int) -> None:
    """Presses space to arm a recording, waits past MIN_RECORD_SECONDS, presses space
    again to arm the stop, and waits for the resulting completed-tile count - the same
    two-press "arm on this tick, apply on the next" flow a real performer uses (see
    `WallDriver.toggle`'s own docstring for why a press never acts immediately).
    """
    pending = page.locator(".tile.pending")
    page.keyboard.press("Space")
    expect(pending).to_have_class(re.compile(r"\brecording\b"), timeout=5000)
    page.wait_for_timeout(_MIN_RECORD_MS)
    page.keyboard.press("Space")
    expect(page.locator(".tile.playing")).to_have_count(expected_tile_count, timeout=_EXTRACTION_TIMEOUT_MS)


def test_the_wall_from_empty_to_a_growing_grid(page: Page, wall_url: str) -> None:
    """The exact flow COOKBOOK.md recipe 4 describes: load the page, see a live-preview
    tile immediately, arm and record a take with the spacebar, watch it become a
    permanently looping tile, then record a second take and watch the grid grow to two -
    a new track per take, not a re-recording of the first (PLAN.md §3.7's own point about
    what makes this a real slice of the §3.5 wall, not a mockup).
    """
    page.goto(f"{wall_url}/wall")

    # The pending tile is on screen from the start - a live-preview viewfinder, not a
    # blank page waiting for the first take.
    pending = page.locator(".tile.pending")
    expect(pending).to_be_visible()
    expect(pending).to_have_class(re.compile(r"\bready\b"))
    expect(pending.locator(".label")).to_have_text("ready - press space")

    # Space arms a recording - it doesn't start immediately, it's applied on the
    # internal ticker's next tick (TICK_SECONDS=0.2s in WallDriver). That window is too
    # short to reliably observe the transient "armed-start" class from here (the DOM's
    # own 200ms state-poll cadence races it) - the durable, meaningful assertion is the
    # state it settles into, not the mid-flight one.
    page.keyboard.press("Space")
    expect(pending).to_have_class(re.compile(r"\brecording\b"), timeout=5000)
    expect(pending.locator(".label")).to_contain_text("recording...")

    page.wait_for_timeout(_MIN_RECORD_MS)
    page.keyboard.press("Space")

    # Stopping cuts the clip (a real ffmpeg extraction) - the pending tile goes back to
    # "ready" while a new completed tile appears with its own clip and duration.
    completed = page.locator(".tile.playing")
    expect(completed).to_have_count(1, timeout=_EXTRACTION_TIMEOUT_MS)
    expect(pending).to_have_class(re.compile(r"\bready\b"))
    expect(completed.locator(".label")).to_contain_text("track 1")
    expect(completed.locator("video")).to_have_attribute("src", re.compile(r"^/clips/"))

    # The status line's own clip counter agrees with the grid.
    expect(page.locator("#s-clips")).to_have_text("1")

    # A second take becomes a second track - the grid genuinely grows.
    _record_one_take(page, expected_tile_count=2)
    expect(page.locator("#s-clips")).to_have_text("2")
    expect(completed.nth(1).locator(".label")).to_contain_text("track 2")


def test_clear_removes_one_tile_reset_removes_all(page: Page, wall_url: str) -> None:
    """The x button on a tile and the "Reset all" button are both plain LDP under the
    hood (`WallDriver.clear`/`reset` each emit a `TrackEvent(state="empty")`, the same
    presence-flip rule any other track leaving the wall uses) - from the browser they
    just look like a tile disappearing.
    """
    page.goto(f"{wall_url}/wall")
    completed = page.locator(".tile.playing")

    _record_one_take(page, expected_tile_count=1)
    page.locator(".tile.playing .clear").click()
    expect(completed).to_have_count(0)
    expect(page.locator("#s-clips")).to_have_text("0")

    _record_one_take(page, expected_tile_count=1)
    _record_one_take(page, expected_tile_count=2)
    page.locator("#btn-reset").click()
    expect(completed).to_have_count(0)
    expect(page.locator("#s-clips")).to_have_text("0")


def test_click_to_record_button_mirrors_the_spacebar(page: Page, wall_url: str) -> None:
    """The pending tile and a labeled status-line button are both just another way to
    reach the same `toggle()` the spacebar already calls - proven here by using only
    the button, never the keyboard, and getting the identical result recipe 4's
    keyboard-only walkthrough gets.
    """
    page.goto(f"{wall_url}/wall")
    record_btn = page.locator("#btn-record")
    expect(record_btn).to_have_text("start recording")

    record_btn.click()
    expect(record_btn).to_have_text("stop && loop it", timeout=5000)
    expect(page.locator(".tile.pending")).to_have_class(re.compile(r"\brecording\b"))

    page.wait_for_timeout(_MIN_RECORD_MS)
    record_btn.click()

    expect(page.locator(".tile.playing")).to_have_count(1, timeout=_EXTRACTION_TIMEOUT_MS)
    expect(record_btn).to_have_text("start recording")


def test_session_info_is_shown_and_changes_on_new_session(page: Page, wall_url: str) -> None:
    """The status line's session id is real, not a placeholder - and starting a new
    session (confirmed via the browser's own confirm() dialog, same as a real
    performer would see) both changes it and clears the grid, without needing to
    restart `loopwall play` itself.
    """
    page.goto(f"{wall_url}/wall")
    session_el = page.locator("#s-session")
    expect(session_el).not_to_have_text("-")
    first_session_id = session_el.text_content()

    _record_one_take(page, expected_tile_count=1)

    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#btn-new-session").click()

    expect(page.locator(".tile.playing")).to_have_count(0, timeout=5000)
    expect(session_el).not_to_have_text(first_session_id or "")

    # The wall still works after a new session - not a one-shot reset - and the new
    # take's clip is genuinely reachable (server/app.py's /clips route is
    # session-scoped, not bound to whichever session existed when the app started).
    _record_one_take(page, expected_tile_count=1)
    clip_src = page.locator(".tile.playing video").get_attribute("src")
    assert clip_src is not None
    response = page.request.get(f"{wall_url}{clip_src}")  # clip_src is relative
    assert response.ok


def test_labeling_a_tile(page: Page, wall_url: str) -> None:
    """Clicking a completed tile's label opens a plain browser prompt() - vanilla JS,
    no framework, matching this page's own existing style - and the new label
    replaces the default "track N" text (duration always stays).
    """
    page.goto(f"{wall_url}/wall")
    _record_one_take(page, expected_tile_count=1)
    tile_label = page.locator(".tile.playing .label")
    expect(tile_label).to_contain_text("track 1")

    page.once("dialog", lambda dialog: dialog.accept("intro loop"))
    tile_label.click()

    expect(tile_label).to_have_text(re.compile(r"^intro loop - \d"))


def test_reordering_two_tiles(page: Page, wall_url: str) -> None:
    """The move-left/move-right buttons reorder the grid itself, not just internal
    bookkeeping - verified by reading the actual DOM order back out, not just the
    /wall/state JSON the buttons post to (applyState()'s own DOM-reordering fix is
    what this test would have caught, had it been missing).
    """
    page.goto(f"{wall_url}/wall")
    _record_one_take(page, expected_tile_count=1)
    _record_one_take(page, expected_tile_count=2)

    completed = page.locator(".tile.playing")
    expect(completed.nth(0).locator(".label")).to_contain_text("track 1")
    expect(completed.nth(1).locator(".label")).to_contain_text("track 2")

    completed.nth(1).locator(".move-left").click()

    expect(completed.nth(0).locator(".label")).to_contain_text("track 2")
    expect(completed.nth(1).locator(".label")).to_contain_text("track 1")


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


def test_importing_a_local_file_adds_a_tile(page: Page, wall_url: str, tmp_path: Path) -> None:
    """The file selector isn't only for camera setup - a pre-recorded clip can join the
    grid directly, no capture involved at all (`WallDriver.import_clip`), through the
    same file input browsers already give you for any upload.
    """
    tiny = tmp_path / "b-roll.mp4"
    _make_tiny_video(tiny)

    page.goto(f"{wall_url}/wall")
    page.set_input_files("#file-import", str(tiny))
    page.locator("#btn-import").click()

    completed = page.locator(".tile.playing")
    expect(completed).to_have_count(1, timeout=10_000)
    expect(completed.locator("video")).to_have_attribute("src", re.compile(r"^/clips/"))
    expect(page.locator("#s-clips")).to_have_text("1")


def test_rendering_a_composite_produces_a_playable_link(page: Page, wall_url: str) -> None:
    """ "Render composite" stacks whatever's currently on the grid into one video
    (`capture/composite.py`) - a background job the status line reflects as it
    progresses, not something that blocks the page while ffmpeg runs.
    """
    page.goto(f"{wall_url}/wall")
    _record_one_take(page, expected_tile_count=1)

    page.locator("#btn-render").click()
    render_link = page.locator("#s-render a")
    expect(render_link).to_be_visible(timeout=30_000)

    href = render_link.get_attribute("href")
    assert href is not None
    response = page.request.get(f"{wall_url}{href}")
    assert response.ok


def test_status_line_is_honest_about_no_midi_configured(page: Page, wall_url: str) -> None:
    """No `--midi` port was requested for this test's driver (see `conftest.py`'s
    `wall_url` fixture), so the status line should read a plain "off", distinct from a
    requested-but-failed connection - the same distinction `WallDriver.state()` draws
    between `midi: None` and a populated `midi_error`. A fast, recording-free test:
    useful as a quick sanity check that the page itself loads and polls correctly even
    when nothing else is proven.
    """
    page.goto(f"{wall_url}/wall")
    expect(page.locator("#s-midi")).to_have_text("midi: off")
    expect(page.locator("#btn-midi-learn")).to_be_hidden()
