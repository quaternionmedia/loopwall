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
