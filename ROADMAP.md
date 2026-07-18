# loopwall — Roadmap

| | |
|---|---|
| **Standing** | Authorized by `PLAN.md` ratification. Each release ships only when its deliverables fence diffs clean (C1) and its gate passes. |
| **Version** | 0.0.2 · **Date** 2026-07-17 |

**Milestone 1 (0.1.0) is completely internal**: no Hapax, no external hardware or software beyond a local UVC webcam. No MediaMTX, no renderer, no browser. The CLI driver is the only driver; everything else is a registered stub (`STUBS.md`). The Hapax V-track (`VERIFICATION.md`) runs in parallel on its own clock and gates 1.0.0 only — nothing in 0.1.0–0.3.0 depends on it.

**Ahead-of-schedule spikes** (validated against real hardware, outside the formal build order below, not gating anything): `capture/ring.py` + `capture/extract.py` and the CLI monitor's Tier 0 (capture-health view, PLAN.md §3.6) exist and work today. They don't change the 0.1.0 gate or the "types before behavior" ordering rationale for steps 1–9 — those still govern what *completes* milestone 1. The CLI's interactive onboarding mode (`loopwall play` with no `SCENARIO`, PLAN.md §3.7) started as a similar spike (`demo.py`, bypassing LDP/core entirely) but has since been rebuilt as the real `cli` driver's REPL half, going through `Core` for real — it is no longer a bypass, and is now part of step 5's own deliverable rather than outside it. It has also grown a real, ahead-of-schedule slice of the §3.5 wall itself — a live-updating browser grid (`/wall`) built from actual committed core state, not a mockup — without pulling the full 0.2.0 renderer scope (MediaMTX/WHEP, quantized cross-fades, layout presets) forward with it; those stay 0.2.0, tracked in `STUBS.md`.

| Release | Deliverables fence | Gate |
|---|---|---|
| **0.0.x** | this corpus; repo scaffolded, `governance/qm` submodule wired (`project/loopwall` branch); CI wired (ruff · pytest+coverage ratchet · conformance replay · gitleaks · placeholder sweep with `STUBS.md` allowlist · ADR squash lint against `governance/qm/adr/DRAFT-*.md`); ADRs seeded; stubs in place | plan ratified; CI green on empty skeleton |
| **0.1.0** | `ldp` models + wire (full catalog defined; only hello/transport/track/capture_cue handled) · timeline (internal metronome, bar→t_mono) · track registry · minimal scheduler (bar-committed track ops → session log only) · **cli driver** (REPL + scenario playback) · capture ring (FFmpeg segments, tmpfs, rolling deletion; `uvc` and `lavfi` camera types) · extractor (x264, realized-timestamp windows) · session artifact (JSONL + clips + index.json) · conformance kit v1 · `scenarios/smoke.yaml` · **CLI monitor Tier 1** (transport + track state, PLAN.md §3.6) | **Integration test below**, plus CI twin green |
| **0.2.0** | renderer (pool, packed layout, sticky seats, quantized transitions, drift correction) · MediaMTX WHEP preview + rec-tile + handover · `accent` + `clip_bind` handlers · `loopwall calibrate` · `/wall` `/console` live | clips from a 0.1.0 session play seated and boundary-locked; handover imperceptible; calibration repeatable ±1 frame |
| **0.3.0** | degradation ladder · console masks · `scenarios/dress-run.yaml` (~30 min) | **CLI dress run**: full set from empty wall, ≥ 30 min soak, no dropped commits |
| **1.0.0** | **hapax driver** (inference tuned to discharged F1–F6) · ops checklist · HANDOFF final | hardware dress rehearsal: live-loop a set on the Hapax |
| **1.1.0** | **showrunner plugin** · `scene` handler + reconciler | cue-driven show mixing capture takes and prepped media |

## Milestone 1 integration gate (verbatim)

On a development machine with any UVC webcam, headless:

```
uv run loopwall play scenarios/smoke.yaml --headless --config config.example.yaml
```

Assertions, scripted in `tests/integration/test_smoke.py` (marked `hardware`; the CI twin substitutes `camera: lavfi` and additionally asserts burned-timecode windows):

1. A session directory exists containing the LDP JSONL log, one clip per `record` directive, and a schema-valid `index.json` with correct active takes.
2. Each clip's duration equals bars × beats × 60/bpm within ±1 frame (33 ms at 30 fps).
3. Extraction latency (trigger → registered) is logged and ≤ 3 s.
4. `loopwall replay <session>` reproduces an identical track-state timeline and identical committed-op sequence (determinism).
5. The ring directory is bounded for the whole run (rolling deletion observed; no growth after steady state).
6. Re-running the scenario produces a new session; nothing in the old one is touched.

The gate is passed when the hardware run and the CI twin both hold, the fence diffs clean, coverage meets the ratchet, and `HANDOFF.md` is rewritten cold-start-complete.

**Status (2026-07-17):** the CI twin (`test_ci_twin_lavfi_smoke_gate`) is built and green, verified twice for stability. The hardware run (`test_real_hardware_smoke_gate`) is written but not yet executed against real hardware this session — the gate isn't fully passed until it is.

## Build order — 0.1.0 (types before behavior)

1. `ldp/models.py` + wire round-trip tests (full catalog; handlers only for the live subset).
2. Conformance harness reading golden streams, asserting committed-op sequences — written red first.
3. `core/timeline.py` (metronome source; boundary math tests, including a tempo-change case).
4. `core/tracks.py` + minimal scheduler + replay-determinism test.
5. `drivers/cli` (REPL + scenario player emitting LDP; scenario schema is Pydantic).
6. `capture/ring.py` (unit-tested against `lavfi`).
7. `capture/extract.py` (timecode CI test).
8. `core/session.py` (index schema, log writer, replay).
9. `tests/integration/test_smoke.py` + CI twin — **built (2026-07-17)**: runs `scenarios/smoke.yaml` unmodified through the real `loopwall play`/`loopwall replay` CLI path, all six of the gate's numbered assertions below, plus a `lavfi`-only pixel-content check standing in for "burned-timecode windows" (PLAN.md §3.4's own wording — an independently-rendered reference frame at each clip's expected ring-relative time, compared via SSIM; see the test module's own docstring for what that can and can't prove). CI twin green, verified twice for stability. **Not done yet**: the real-hardware variant (`test_real_hardware_smoke_gate`, `hardware`-marked) hasn't actually been run this session — camera hardware needed, deliberately not touched here — and no polymeter (overlapping, differently-sized cycles) golden scenario exists; `smoke.yaml`'s own two takes don't overlap in time.
10. CLI monitor Tier 1 (PLAN.md §3.6) — **built (2026-07-17)**, differently than originally scoped here: not a standalone `loopwall monitor` mode (two processes can't share one camera, so it could never actually see a live driver's state that way), but `loopwall play <scenario> --monitor` - the scripted path already owns both the ring and a live `Core` in one process. `CliDriver` gained an `on_tick` hook (fires once per bar); `monitor/state.py`'s `monitor_state_from_core()` and `monitor/render.py`'s Tier 1 rendering read straight off `core.tracks`/`core.last_transport`, same "no separate state machine" contract Tier 0 already had.

Definition of done, every release: fence diff clean · tests green at or above the coverage ratchet · no placeholders outside `STUBS.md` · gate evidence stored in a session artifact · `HANDOFF.md` cold-start-complete · tag pushed by the human key (D1).
