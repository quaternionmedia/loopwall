"""The wall's own splash demo, as a real test - the visual introduction referenced by
`README.md` and walked through in `COOKBOOK.md` recipe 8: an empty wall fills up with
nine distinct, independently-looping tiles until the grid is a full 3x3, jams for a
beat, then renders into `assets/demo.gif` - the "git friendly" format GitHub inlines
directly from a relative repo path, no extra hosting and no player controls, unlike a
committed `.mp4`.

`lavfi` only, same as every other file in this directory - no camera or real hardware
touched. Import, not live record: nine takes through the real record/extract path
would be slower and less deterministic, and `WallDriver.switch_camera()` doesn't
currently accept a custom `lavfi_source` through the API, so every take would look
identical anyway. `WallDriver.import_clip()` already does exactly what's needed - a
pre-made file becomes a new, permanently-looping tile, no capture window at all - and
is the same path `test_wall_tutorial.py`'s own import test already exercises.

By default this only ever writes to `tmp_path` - a normal, side-effect-free pytest
run. Set `LOOPWALL_SPLASH_GIF_OUT` to also copy the result to a real path (how a
human deliberately refreshes the committed `assets/demo.gif` - see COOKBOOK.md recipe
8 for the exact command on each platform).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from loopwall.capture.composite import render_gif

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")

_TILE_COUNT = 9
_CLIP_SECONDS = 2.5
_JAM_MS = 3000  # a real but CI-sized pause, standing in for a longer real jam session
_EXTRACTION_TIMEOUT_MS = 15_000


def _click_tab(page: Page, name: str) -> None:
    """Settings (Session/Camera/MIDI/Render, wall/index.html's initTabs()) is a real
    ARIA tabs pattern - only the active tab's panel is visible/interactable, so any
    test touching a moved control needs this first or hits Playwright's own
    actionability check on a hidden element.
    """
    page.get_by_role("tab", name=name).click()


def _make_shape_clip(path: Path, hue_degrees: float, seconds: float = _CLIP_SECONDS) -> None:
    """One of the grid's nine "shapes": this project's own standard synthetic test
    source (`testsrc2` - already used throughout `tests/`), tinted a distinct color
    via ffmpeg's `hue` filter (`s=2` boosts saturation so the tint reads clearly, not
    subtly). Real motion, since `testsrc2` is itself an animated pattern - a tile
    that's visibly looping, not a frozen frame - plus an obvious color identity per
    tile, no new dependency beyond the ffmpeg this whole project already requires.
    """
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=30",
            "-vf",
            f"hue=h={hue_degrees}:s=2",
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


def _frame_count(path: Path) -> int:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    return int(result.stdout.strip())


def test_the_intro_demo_fills_a_nine_tile_grid_and_renders_a_splash_gif(
    page: Page, wall_url: str, tmp_path: Path
) -> None:
    """The full splash demo, narrated:

    1. The wall loads empty - just the live-preview pending tile.
    2. Nine distinctly-hued clips are imported one at a time (Session tab), each
       landing as its own permanently-looping tile - the grid genuinely grows to a
       full 3x3 (`layoutGrid`'s own `cols = ceil(sqrt(9)) = 3`, PLAN.md §3.5's packed
       layout, unmodified - nothing here is a mockup of the real layout).
    3. Once at nine, everything just plays together for a beat - every tile is
       already an autoplaying, looping `<video>`, so nothing further is *done* to
       make them "jam"; this is a pacing beat, not an action.
    4. "Render composite" (Render tab, the existing, already-tested control) stacks
       the nine loops into one video, looped to match.
    5. That video is converted to an animated GIF - the format `README.md`'s splash
       image and COOKBOOK.md recipe 8 both point at.
    """
    page.goto(f"{wall_url}/wall")

    pending = page.locator(".tile.pending")
    expect(pending).to_be_visible()

    _click_tab(page, "Session")
    completed = page.locator(".tile.playing")
    for i in range(_TILE_COUNT):
        clip = tmp_path / f"shape_{i}.mp4"
        _make_shape_clip(clip, hue_degrees=i * 40)
        page.set_input_files("#file-import", str(clip))
        page.locator("#btn-import").click()
        expect(completed).to_have_count(i + 1, timeout=_EXTRACTION_TIMEOUT_MS)

    expect(page.locator("#s-clips")).to_have_text(str(_TILE_COUNT))

    # Jam: nine independently-looping tiles, all on screen together.
    page.wait_for_timeout(_JAM_MS)
    expect(completed).to_have_count(_TILE_COUNT)

    _click_tab(page, "Render")
    page.locator("#btn-render").click()
    render_link = page.locator("#s-render a")
    expect(render_link).to_be_visible(timeout=30_000)

    href = render_link.get_attribute("href")
    assert href is not None
    response = page.request.get(f"{wall_url}{href}")
    assert response.ok
    composite_path = tmp_path / "composite.mp4"
    composite_path.write_bytes(response.body())

    gif_path = tmp_path / "demo.gif"
    render_gif(composite_path, gif_path)

    assert gif_path.is_file()
    assert gif_path.stat().st_size > 0
    assert _frame_count(gif_path) > 1  # genuinely animated, not a static frame

    splash_out = os.environ.get("LOOPWALL_SPLASH_GIF_OUT")
    if splash_out:
        dest = Path(splash_out)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(gif_path, dest)
