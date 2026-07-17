# loopwall — HANDOFF

| | |
|---|---|
| **Standing** | The only sanctioned cross-session memory. Rewritten at every gate. A session begins by reading this file and the corpus it points to; chat history is treated as nonexistent. |
| **State as of** | 2026-07-16 |

## Current state

Plan corpus v0.0.2, complete and internally consistent, **awaiting ratification**. Milestone 1 (0.1.0) is fully internal: CLI driver + local UVC webcam, headless — no Hapax, no MediaMTX, no renderer. Everything outside that loop is a registered stub (`STUBS.md`). The Hapax V-track is parked and gates 1.0.0 only.

**Capture/extraction proof of concept (2026-07-16):** ahead of the formal 0.1.0 build order, `capture/ring.py` and `capture/extract.py` (ROADMAP.md build order steps 6–7) got a real implementation and were validated against a real webcam (dshow on this Windows dev machine — production targets UVC/V4L2 on Linux; a `tests/integration/test_capture_poc.py`, `hardware`-marked, exercises this and is not part of any CI gate). Result: continuous ring capture + after-the-fact window extraction works — a requested 4.000s window came back as 3.900s (100 ms short), which touches the retrospective's falsifiable prediction #2 (`perspectives/2026-07-16-plan-retrospective.md`: "the ±1-frame duration assertion fails first on segment/GOP boundary rounding, not camera latency") — 100 ms is 3 frames at 30 fps, past the plan's ±1-frame target, and worth root-causing before the real `tests/integration/test_smoke.py` gate is written. Camera-safety note for future hardware work: `ffmpeg` on PATH here resolves to a Chocolatey launcher shim that spawns the real binary as a child process — killing only the tracked PID orphans a live capture with the camera still open (discovered the hard way, mid-session). `RingCapture.stop()` now kills the whole process tree (`taskkill /T` on Windows, `killpg` on POSIX), not just one PID; still not yet run against a `lavfi` synthetic source, which is what steps 6–7's own definition of done (ROADMAP.md) actually requires for the CI-facing path.

**Duration compensator, on your instruction (2026-07-16):** rather than root-causing the 100 ms gap now, `capture/extract.py` gained `DurationCompensator` — a session-scoped, explicitly-provisional rolling correction (no ADR; flagged for review/replacement once root-caused) that nudges a loop's requested end time by the previous loop's observed bias. `test_capture_poc.py` was rewritten to run it across two sequential real-webcam extractions and *measure*, not assert, whether it helps. It didn't converge: one real run showed no improvement at all (loop 2 still ~100 ms short despite asking for 100 ms more); a second showed a ~100 ms *overshoot* in the other direction. Together those rule out "fixed additive bias" as the model — something is quantizing the cut in a way that varies run to run, not a constant this stop-gap can be tuned against. Left in place as a real, working mechanism and an honest negative result, per your call to defer investigation rather than solve it now.

Still to do for a complete step 6–7: a `lavfi` (testsrc, burned timecode) path through the same `RingCapture`/`extract_window` API, and the actual root-cause investigation into the duration bias (parked, not forgotten).

**Governance migration (2026-07-16, mid-session):** the qm org corpus moved to a submodule-plus-branch ADR model (see `governance/qm/README.md`'s "Forking a new project") before this plan corpus was drafted; the corpus's `records/`-in-repo design was written against the superseded model. Reconciled: loopwall now vendors `qm` as a submodule at `governance/qm`, pinned to this project's own `project/loopwall` branch of that repo (created off `main` tip `4541f92`). This project's ADRs live there as `governance/qm/adr/DRAFT-*.md` — `DRAFT-ldp1-driver-seam.md`, `DRAFT-ring-capture-semantics.md`, `DRAFT-dependency-disposition.md` (content unchanged from the former `ADR-0001..0003`, renumbered to numberless drafts-by-title per the discipline) plus a new `DRAFT-constitution-adoption-scope.md`. The three renamed ADRs are still what `PLAN.md`'s ratification checkbox ratifies; the adoption-scope draft pends org-level ratification separately (see its own `Pends on`). Local `qm` was also 10 commits stale and has been fast-forwarded to `origin/main`. Not yet pushed to GitHub — this branch and the loopwall repo itself exist only locally pending review.

## Next three actions

1. **Ratify** (human key): `PLAN.md` sign-off block; the three renamed `governance/qm/adr/DRAFT-*.md` project ADRs ratify with it. Amendments are new ADRs, squash-clean. Ratification-track review is artifact-only (E1) — judge the corpus, not the drafting narrative.
2. **Scaffold** (release 0.0.x): lay down the tree below; wire CI (ruff · pytest + coverage ratchet · conformance replay · gitleaks · placeholder sweep reading `STUBS.md` as its allowlist · `adr-lint.yml` ADR squash lint, submodule-aware); copy `project-seed/ide/` governance-discovery files (`AGENTS.md` etc.); commit stubs and this corpus; create `quaternionmedia/loopwall` on GitHub and push both it and the `project/loopwall` branch of `qm`. Gate: CI green on the empty skeleton.
3. **Build 0.1.0** in the order given in `ROADMAP.md` (types before behavior, conformance harness red first). Gate: the milestone-1 integration test, verbatim in `ROADMAP.md`, on a real webcam plus its `lavfi` CI twin.

## Repo tree (target at 0.0.x)

```
loopwall/
├── governance/qm/  submodule, pinned to project/loopwall — adr/ holds this project's ADRs
├── AGENTS.md  CLAUDE.md  .github/copilot-instructions.md  .vscode/  (IDE-integrated governance discovery)
├── PLAN.md  ROADMAP.md  VERIFICATION.md  STUBS.md  HANDOFF.md
├── perspectives/   attributed, dated, non-binding
├── pyproject.toml  (uv-managed, py3.12, deps pinned)
├── src/loopwall/
│   ├── ldp/        models.py · wire.py
│   ├── core/       timeline.py · tracks.py · scheduler.py · session.py
│   │               reconciler.py (stub)
│   ├── drivers/    base.py · cli/ · hapax/ (stub) · showrunner.py (stub)
│   ├── capture/    ring.py · extract.py · calibrate.py (stub)
│   └── server/     app.py (/driver live; /wall /console 501)
├── wall/           (absent until 0.2.0)
├── conformance/    golden LDP streams → expected committed-op sequences
├── scenarios/      smoke.yaml (+ dress-run.yaml at 0.3.0)
├── tests/          unit · property · integration/test_smoke.py (hardware-marked)
└── config.example.yaml
```

Toolchain: `uv venv && uv sync --all-extras`; everything through `uv run`. Entry points: `loopwall play <scenario> [--headless]` · `loopwall cli` · `loopwall replay <session>` · (`loopwall serve`, `loopwall calibrate` from 0.2.0). System deps for 0.1.0: ffmpeg, v4l2-utils.

## Session protocol

Keep a one-line-per-attempt log; three failures of one approach family → strategy switch or `STUCK.md`, which ends the session honorably (C2). Secrets by reference only (D2). Irreversible acts require the human key; agents present diffs (D1). End every gate by: fence diff against the release's deliverables (C1) · coverage at or above the ratchet (B2) · no placeholders outside `STUBS.md` (B3) · gate evidence stored in a session artifact · **rewrite this file** so a cold session can resume (A2) · human tags the release.

## Open items

None blocking 0.1.0. Parked: F1–F6 (`VERIFICATION.md`) — run opportunistically, must be discharged before 1.0.0. Known ambition ceiling for milestone 1 is deliberate; see `STUBS.md` rationale and the retrospective in `perspectives/`.
