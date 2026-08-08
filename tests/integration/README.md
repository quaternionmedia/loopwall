# tests/integration

`test_smoke.py` — the milestone-1 integration gate, marked `hardware`; the
CI twin substitutes `camera: lavfi`. Written at 0.1.0 build order step 9
(`ROADMAP.md`), against `scenarios/smoke.yaml`. See `ROADMAP.md`'s
"Milestone 1 integration gate (verbatim)" for the exact assertions.

`test_capture_poc.py` — an early, standalone proof of concept for
`capture/ring.py` + `capture/extract.py` (build order steps 6–7) against a
real webcam, predating `test_smoke.py` and not gating anything; see
`HANDOFF.md`'s "Capture/extraction proof of concept" note. `hardware`-marked,
excluded from CI.

`test_core_conformance_lite.py` — hand-authored LDP events fed straight to
`Core.handle_event` (not through `CliDriver`), replaying `scenarios/smoke.yaml`'s
exact bar shape at a compressed tempo against a real `lavfi` ring; asserts
the resulting committed-op sequence and session artifact. Stands in for the
still-unbuilt golden-stream conformance kit (`conformance/README.md`,
`ROADMAP.md` build order step 2) — not `hardware`-marked, skips cleanly if
`ffmpeg` is missing.

`test_play_lifecycle.py` — `loopwall play`'s CLI surface via Click's
`CliRunner`: scripted scenario playback (`--config`), the no-`SCENARIO`
path reaching `InteractiveDriver`/`WallDriver`, `--monitor`, and the
scenario-without-`--config` `UsageError` case.

`test_monitor_lifecycle.py` — the Tier 0 CLI monitor (`loopwall monitor`,
PLAN.md §3.6) against a real `lavfi` ring end to end: render output and
clean teardown. Not `hardware`-marked, skips cleanly without `ffmpeg`.

`test_wall_app.py` — the browser wall's FastAPI app (`server/app.py`) end
to end via `TestClient`, driving its real lifespan (`WallDriver.start()`/
`stop()`): page/state/control endpoints, arm-then-record-then-loop over a
real `lavfi` ring and `PreviewRelay`, clip serving, reset, camera
switching, local-file import, session save/load, config export, and the
composite-render pipeline. Not `hardware`-marked.

`test_composite.py` — `capture/composite.py`'s `composite_grid()` directly
against real ffmpeg: multi-clip and single-clip layouts, and the specific
regression case (a clip carrying an audio track) that broke an earlier
`-stream_loop`-based version in the field — see that module's own
docstring for the root cause. Not `hardware`-marked.

`_content_check.py` — not a test module; the shared burned-timecode SSIM
comparison `test_smoke.py` and `test_polymeter.py` both import, factored
out once the second caller needed the identical ~80-line check. See its
own docstring for the SSIM threshold's empirical calibration.

`test_polymeter.py` — `scenarios/polymeter.yaml` (`ROADMAP.md` build order
step 9): two genuinely overlapping, differently-sized captures (a 3-bar
track-1 take and a 4-bar track-2 take, both open from bar 3 to bar 5),
verifying the per-track pending-capture bookkeeping keeps concurrent
windows straight (each clip's content matches only its own window, via
`_content_check.py`) and that the presence-flip op rule still holds with a
completed take sitting next to a still-open second track. `lavfi`-only,
same as `test_smoke.py`'s CI twin.
