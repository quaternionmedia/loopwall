# conformance

Golden LDP streams paired with their expected committed-op sequences,
replayed through core in CI to keep every driver honest against one core
(`PLAN.md` §3.1). The harness is written red-first at 0.1.0 build order
step 2 (`ROADMAP.md`), before `core/timeline.py` exists — golden streams
land alongside it, including a polymeter scenario (build order step 9).
