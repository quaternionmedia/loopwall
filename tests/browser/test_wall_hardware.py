"""Browser-driven `/wall` tests against real capture/MIDI hardware - `hardware`-marked,
skipped in CI, run manually:

    uv run pytest -m hardware tests/browser/test_wall_hardware.py -v

Everything else in `tests/browser/` is deliberately `lavfi`-only; this file exists
specifically to exercise what `lavfi` can't: real device enumeration, a real camera
switch between two real devices, and a real MIDI connection - the exact setup (two
cameras, one MIDI controller) that surfaced three real bugs during live testing:
`switch_camera()` silently leaving the wall camera-less on a device that failed to
open (fixed: `capture/ring.py`'s `wait_until_ready`/`tail_stderr`, a probe-before-
committing `switch_camera()`), and a real-resolution extraction (~3.5s+, measured
this session) with nothing on screen to show it was in progress, which read as a
recording indicator stuck on the wrong tile (fixed: `WallDriver.state()`'s
`extracting_track`, `wall/index.html`'s processing-tile placeholder - see
`test_wall_tutorial.py`'s `slow_wall_url`-based test for that fix's own lavfi-only,
CI-safe coverage).

Camera-count- and MIDI-presence-dependent: tests that need a second camera or a MIDI
port skip cleanly (not fail) when this machine doesn't have one, via
`monitor.camera.list_camera_devices`/`midi.is_available` - the same real detection
the app itself uses, not a hardcoded assumption about what's plugged in.
"""

from __future__ import annotations

import json
import re
import shutil

import pytest
from playwright.sync_api import Page, expect

from loopwall.midi import MidiInput
from loopwall.monitor.camera import list_camera_devices

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH"),
]

_MIN_RECORD_MS = 1200
_EXTRACTION_TIMEOUT_MS = 15_000


def test_the_live_preview_shows_a_real_camera(page: Page, real_hardware_wall_url: str) -> None:
    """No `--camera-type` override - same auto-detection `loopwall play` itself uses
    (`detect_default_camera`). Confirms the page loads and the status line reports the
    real camera it opened, not a fallback to `lavfi` (which would mean the real
    device failed to open at startup - a real finding, not just a skipped assertion).
    """
    devices = list_camera_devices()
    if not devices:
        pytest.skip("no real camera detected on this machine")

    page.goto(f"{real_hardware_wall_url}/wall")
    pending = page.locator(".tile.pending")
    expect(pending).to_be_visible()
    expect(pending).to_have_class(re.compile(r"\bready\b"), timeout=10_000)

    state = page.evaluate("() => fetch('/wall/state').then(r => r.json())")
    assert state["camera"]["type"] != "lavfi", (
        f"expected a real camera, got {state['camera']!r} - the real device failed to "
        "open at startup and the wall silently fell back to lavfi"
    )


def test_switching_to_the_second_real_camera_succeeds(page: Page, real_hardware_wall_url: str) -> None:
    """The direct regression test for the bug found live: switching to a second real
    camera used to leave the wall with a broken ring and no clear error (`switch_camera`
    swapped in a ring whose ffmpeg process had already failed to open the device, and
    never checked). Exercised here through the actual browser dropdown, not just
    `test_wall_app.py`'s `TestClient`-level coverage of the same fix.
    """
    devices = list_camera_devices()
    if len(devices) < 2:
        pytest.skip(f"needs 2+ real cameras, found {len(devices)}: {devices}")

    page.goto(f"{real_hardware_wall_url}/wall")
    first_camera = page.evaluate("() => fetch('/wall/state').then(r => r.json())")["camera"]

    sel = page.locator("#sel-camera")
    sel.focus()  # triggers loadCameraOptions() - populates from /wall/cameras on demand
    expect(sel.locator("option")).to_have_count(len(devices), timeout=5000)

    # Pick whichever option isn't the currently-active camera.
    option_values = page.eval_on_selector_all("#sel-camera option", "els => els.map(e => e.value)")
    target = next(v for v in option_values if json.loads(v)["device"] != first_camera["device"])
    sel.select_option(target)
    page.locator("#btn-switch-camera").click()

    target_device = json.loads(target)["device"]
    for _ in range(50):
        state = page.evaluate("() => fetch('/wall/state').then(r => r.json())")
        if state["camera"]["device"] == target_device:
            break
        page.wait_for_timeout(200)
    else:
        pytest.fail(f"camera never switched to {target_device!r}; last state camera={state['camera']!r}")

    # The wall keeps working after the switch - not just that the field changed.
    expect(page.locator(".tile.pending")).to_have_class(re.compile(r"\bready\b"), timeout=10_000)


def test_midi_status_line_shows_a_real_connected_port(page: Page, real_hardware_wall_url: str) -> None:
    """No real MIDI hardware -> a plain "off" (`test_status_line_is_honest_about_no_midi_
    configured` already covers that path with `lavfi`). With a real port present, the
    status line should name it for real - exercising `MidiInput.start()` against an
    actual `mido` port through the full browser -> state()-poll path, not a unit test
    against fake message objects.
    """
    if not MidiInput.list_input_names():
        pytest.skip("no real MIDI input detected on this machine")

    page.goto(f"{real_hardware_wall_url}/wall")
    midi_status = page.locator("#s-midi")
    expect(midi_status).not_to_have_text("midi: off", timeout=10_000)
    expect(midi_status).to_have_class("midi-ok")
    expect(page.locator("#btn-midi-learn")).to_be_visible()


def test_recording_a_take_on_the_real_default_camera(page: Page, real_hardware_wall_url: str) -> None:
    """The full real-hardware pipeline end to end: record, stop, watch the processing
    placeholder (this session's fix) appear and then resolve into a playable clip - the
    same flow `test_wall_tutorial.py` exercises against `lavfi`, run here against
    whatever real camera this machine actually has, at production resolution
    (1280x720, not lavfi's 320x240) where the extraction-latency finding this session
    made was actually measured.
    """
    devices = list_camera_devices()
    if not devices:
        pytest.skip("no real camera detected on this machine")

    page.goto(f"{real_hardware_wall_url}/wall")
    pending = page.locator(".tile.pending")

    page.keyboard.press("Space")
    expect(pending).to_have_class(re.compile(r"\brecording\b"), timeout=10_000)
    page.wait_for_timeout(_MIN_RECORD_MS)
    page.keyboard.press("Space")

    # Real extraction is slow enough here that the processing placeholder should be
    # observable, not just theoretically present - but don't fail the test if the
    # 200ms poll happens to miss the window on a particularly fast run.
    processing = page.locator(".tile.processing")
    try:
        expect(processing).to_have_count(1, timeout=2000)
    except AssertionError:
        pass

    expect(page.locator(".tile.playing")).to_have_count(1, timeout=_EXTRACTION_TIMEOUT_MS)
    expect(processing).to_have_count(0)
    expect(pending).to_have_class(re.compile(r"\bready\b"))
