# loopwall — Roadmap

| | |
|---|---|
| **Standing** | Authorized by `PLAN.md` ratification. Each release ships only when its deliverables fence diffs clean (C1) and its gate passes. |
| **Version** | 0.0.2 · **Date** 2026-07-16 |

**Milestone 1 (0.1.0) is completely internal**: no Hapax, no external hardware or software beyond a local UVC webcam. No MediaMTX, no renderer, no browser. The CLI driver is the only driver; everything else is a registered stub (`STUBS.md`). The Hapax V-track (`VERIFICATION.md`) runs in parallel on its own clock and gates 1.0.0 only — nothing in 0.1.0–0.3.0 depends on it.

**Ahead-of-schedule spikes** (validated against real hardware, outside the formal build order below, not gating anything): `capture/ring.py` + `capture/extract.py`, the CLI monitor's Tier 0 (capture-health view, PLAN.md §3.6), and the CLI demo (manual record→extract→play loop, PLAN.md §3.7) exist and work today. They don't change the 0.1.0 gate or the "types before behavior" ordering rationale for steps 1–9 — those still govern what *completes* milestone 1. The demo in particular exercises capture/extraction directly, bypassing LDP/core entirely — it is not a preview of `loopwall play` and doesn't reduce steps 1–5's scope.

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

## Build order — 0.1.0 (types before behavior)

1. `ldp/models.py` + wire round-trip tests (full catalog; handlers only for the live subset).
2. Conformance harness reading golden streams, asserting committed-op sequences — written red first.
3. `core/timeline.py` (metronome source; boundary math tests, including a tempo-change case).
4. `core/tracks.py` + minimal scheduler + replay-determinism test.
5. `drivers/cli` (REPL + scenario player emitting LDP; scenario schema is Pydantic).
6. `capture/ring.py` (unit-tested against `lavfi`).
7. `capture/extract.py` (timecode CI test).
8. `core/session.py` (index schema, log writer, replay).
9. `tests/integration/test_smoke.py` + CI twin; goldens green, including a polymeter scenario.
10. CLI monitor Tier 1 (PLAN.md §3.6): reads transport + track state from steps 3–4's committed state, extends the Tier 0 capture-health view already built.

Definition of done, every release: fence diff clean · tests green at or above the coverage ratchet · no placeholders outside `STUBS.md` · gate evidence stored in a session artifact · `HANDOFF.md` cold-start-complete · tag pushed by the human key (D1).
