# loopwall

A live-performance video wall for loop-based music. The wall starts empty; when the performer records a loop, a camera take of that pass is extracted from a rolling buffer, cut to one pattern cycle, and becomes that loop's tile. Tiles track the arrangement, quantized to the bar. The arrangement source is a pluggable driver: internal CLI, Squarp Hapax over MIDI, or a cue engine (ShowRunner).

**Status:** plan corpus v0.0.2, pending ratification (kept local — no git
remote yet; see `HANDOFF.md`'s "Next three actions"). Milestone 1 (0.1.0,
`ROADMAP.md`) is code-complete, its CI twin is green (including a polymeter
scenario exercising overlapping captures), and the gate has now run against
real capture hardware — four of its five numbered assertions pass clean;
the fifth (extraction latency ≤3s) doesn't hold at real 1280x720 resolution
on this dev machine (~3.5-3.7s observed), a real, reproduced, honestly-open
finding, not yet root-caused. A real, ahead-of-schedule slice of the 0.2.0
renderer already works too — `uv run loopwall play` opens a browser wall
with a live camera preview and a growing grid of looping takes,
controllable by spacebar, click, or MIDI. Start at `HANDOFF.md` for the
full cold-start state; `COOKBOOK.md` for the fastest way to run any of it
yourself.

| File | Role |
|---|---|
| `PLAN.md` | Ratifiable architecture and defaults |
| `ROADMAP.md` | Releases, deliverables fences, gates — milestone 1 is fully internal (CLI driver + webcam, headless) |
| `governance/qm/adr/` | This project's ADRs (vendored submodule, `project/loopwall` branch): constitution adoption scope · LDP/1 driver seam · ring capture, first-cycle semantics · dependency disposition |
| `VERIFICATION.md` | Hapax V-track (F1–F6); parked, gates 1.0.0 only |
| `STUBS.md` | Declared stub registry; the B3 placeholder-sweep allowlist |
| `scenarios/smoke.yaml` | The first integration test, as data |
| `config.example.yaml` | Reference configuration |
| `HANDOFF.md` | Cold-start packet; the only sanctioned cross-session memory |
| `perspectives/` | Attributed, dated, non-binding — includes the drafting retrospective |
| `AGENTS.md` | Governance + project entry point for any coding agent (`CLAUDE.md`, `.github/copilot-instructions.md` point to it) |
| `CONTRIBUTING.md` | Index into the above for a first-time contributor |
| `COOKBOOK.md` | Runnable recipes: clone, install, verify the toolchain, run the browser wall, verify capture against a real camera |

Governed by the QM constitution, vendored at `governance/qm` (submodule
pinned to this project's `project/loopwall` branch of that repo — see
`governance/qm/adr/README.md`'s "Adopted org records"). Seeded from
`project-seed/` at scaffold time.
