# loopwall

A live-performance video wall for loop-based music. The wall starts empty; when the performer records a loop, a camera take of that pass is extracted from a rolling buffer, cut to one pattern cycle, and becomes that loop's tile. Tiles track the arrangement, quantized to the bar. The arrangement source is a pluggable driver: internal CLI, Squarp Hapax over MIDI, or a cue engine (ShowRunner).

**Status:** plan corpus v0.0.2, pre-ratification. No code exists. Start at `HANDOFF.md`.

| File | Role |
|---|---|
| `PLAN.md` | Ratifiable architecture and defaults |
| `ROADMAP.md` | Releases, deliverables fences, gates — milestone 1 is fully internal (CLI driver + webcam, headless) |
| `governance/qm/adr/` | This project's ADRs (vendored submodule, `project/loopwall` branch): constitution adoption scope · LDP/1 driver seam · retroactive capture semantics · dependency disposition |
| `VERIFICATION.md` | Hapax V-track (F1–F6); parked, gates 1.0.0 only |
| `STUBS.md` | Declared stub registry; the B3 placeholder-sweep allowlist |
| `scenarios/smoke.yaml` | The first integration test, as data |
| `config.example.yaml` | Reference configuration |
| `HANDOFF.md` | Cold-start packet; the only sanctioned cross-session memory |
| `perspectives/` | Attributed, dated, non-binding — includes the drafting retrospective |
| `AGENTS.md` | Governance + project entry point for any coding agent (`CLAUDE.md`, `.github/copilot-instructions.md` point to it) |

Governed by the QM constitution, vendored at `governance/qm` (submodule
pinned to this project's `project/loopwall` branch of that repo — see
`governance/qm/adr/README.md`'s "Adopted org records"). Seeded from
`project-seed/` at scaffold time.
