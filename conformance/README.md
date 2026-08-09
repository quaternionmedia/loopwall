# conformance

Golden LDP streams paired with their expected committed-op sequences,
replayed through core in CI to keep every driver honest against one core
(`PLAN.md` §3.1) — the target design for this directory, per 0.1.0 build
order step 2 (`ROADMAP.md`).

**Not actually built yet.** This directory is a README only; CI's own
"Conformance replay" step (`.github/workflows/ci.yml`) still no-ops for
exactly that reason. What exists instead, standing in for it at 0.1.0:
`tests/integration/test_core_conformance_lite.py` feeds hand-authored LDP
events straight to `Core.handle_event` (replaying `scenarios/smoke.yaml`'s
exact bar shape at a compressed tempo) and asserts the resulting
committed-op sequence — real coverage of the same property this directory
is meant to guarantee, but as inline Python, not data-driven golden
streams, and not picked up by CI's conformance-specific step. Building
this directory for real — golden stream files plus a harness that reads
them — remains open work, not done. The polymeter scenario build order
step 9 also named (overlapping, differently-sized cycles) is no longer
part of that gap: `scenarios/polymeter.yaml` + `tests/integration/
test_polymeter.py` cover it, the same way `test_core_conformance_lite.py`
covers smoke.yaml's shape — real coverage of the property, as inline
Python against a real `lavfi` ring, not as a golden stream this directory
would read.
