# conformance

Golden LDP streams paired with their expected committed-op sequences,
replayed through core in CI to keep every driver honest against one core
(`PLAN.md` §3.1) — the target design for this directory, per 0.1.0 build
order step 2 (`ROADMAP.md`).

**Real, minimal on purpose.** One golden stream (`golden/smoke.json`),
captured from an actual `loopwall play scenarios/smoke.yaml` run's real
`log.jsonl` — not hand-authored — and `test_golden_streams.py`, which
discovers every `golden/*.json` file, replays its events through
`core.replay.replay_events` (the same driver-agnostic primitive
`loopwall replay <session>` itself uses), and asserts the result against
the `expected_committed_ops` recorded alongside it. CI's own "Conformance
replay" step (`.github/workflows/ci.yml`) runs `pytest conformance -q`
against whatever `test_*.py` it finds here — adding a golden stream or a
test file to this directory needs no CI configuration change.

`tests/integration/test_core_conformance_lite.py` (hand-authored events
straight into `Core.handle_event`) and `tests/integration/test_polymeter.py`
(a real `lavfi` ring, `scenarios/polymeter.yaml`) still exist and still
cover the same underlying property from their own angles — real coverage,
just not data-driven golden streams. This directory doesn't replace them;
it's the first golden-stream-shaped slice of what those two hand-rolled
tests only approximate.

**Deliberately not the full multi-driver system PLAN.md §3.1 eventually
describes** — only one real driver (`cli`) exists in this codebase today,
so there's nothing yet for a second golden stream to prove *cross-driver*
agreement about. What's here proves the harness and the mechanism are
real; expanding it later is adding another `.json` file, not writing new
code.

## Adding another golden stream

Run any real driver scenario, then convert its session log:

```bash
uv run loopwall play scenarios/smoke.yaml --config config.example.yaml
uv run python conformance/_generate_golden.py <session_dir>/log.jsonl <name>
```

This writes `conformance/golden/<name>.json` — commit it like any other
test fixture. `test_golden_streams.py` picks it up automatically on the
next run; no test code changes needed. When hapax (or any future driver)
has its own first real run, its `log.jsonl` becomes a golden stream the
exact same way — this is the "shape for expansion" this directory exists
to demonstrate.
