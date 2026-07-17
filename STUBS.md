# loopwall — Stub registry

| | |
|---|---|
| **Standing** | The B3 placeholder-sweep allowlist. `NotImplementedError` and TODO markers are permitted **only** on paths listed here; each stub raises with a pointer to its plan section. Removing a stub means implementing it in its target release and deleting its row — the row deletion is part of that release's fence diff (C1). Adding a row outside a ratified roadmap change is a lint failure. |
| **Version** | 0.0.2 · **Date** 2026-07-17 |

| Path | Stub behavior at 0.1.0 | Target |
|---|---|---|
| `src/loopwall/drivers/hapax/` | raises — `PLAN §3.2; gated by VERIFICATION.md` | 1.0.0 |
| `src/loopwall/drivers/showrunner.py` | raises — `PLAN §3.2` | 1.1.0 |
| `src/loopwall/core/reconciler.py` | raises on `scene` — models defined, handler absent | 1.1.0 |
| `ldp` handler: `accent` | model defined; core logs-and-drops | 0.2.0 |
| `ldp` handler: `clip_bind` | model defined; core logs-and-drops | 0.2.0 |
| `ldp` handler: `config_hint` | model defined; core logs-and-drops | 0.2.0 |
| `src/loopwall/capture/calibrate.py` | raises — `PLAN §3.4`; `offset_ms` comes from config (default 0) | 0.2.0 |
| `src/loopwall/server` route `/console` | HTTP 501 with plan pointer — `/wall` is real (PLAN §3.7) | 0.2.0 |
| MediaMTX / WHEP preview | absent; live tile is a raw MPEG-TS relay decoded client-side (PLAN §3.7) | 0.2.0 |
| quantized cross-fade handover, drift correction | absent; `/wall` swaps tiles on plain DOM insert, no masking | 0.2.0 |
| `packed`/`fixed16`/`brady9` layout presets, seat stickiness, reflow animation | absent; `/wall`'s JS computes a plain `cols = ceil(sqrt(N))` grid only | 0.2.0 |

Rationale: milestone 1 is deliberately unambitious — the headless capture loop is the risk being retired, and everything that does not serve it is a declared stub rather than a half-implementation. Stubs are honest about their absence; half-implementations lie.
