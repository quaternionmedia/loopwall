# Retrospective on the loopwall plan corpus

| | |
|---|---|
| **Standing** | Perspective — attributed, dated, non-binding. Not part of the ratifiable corpus. |
| **Author** | Peter Kagstrom |
| **Date** | 2026-07-16 |
| **Tools** | Claude Sonnet 5, drafting from the artifacts as they stand |

Per convention, no praise; what holds is stated as load-bearing fact, and the rest is downsides, rejected alternatives, confessions, and predictions.

## What is load-bearing

The shared monotonic timeline (one clock stamping driver events and video frames) is the mechanism everything else leans on — retroactive extraction, ramp-safe cuts, replay determinism. The ring-plus-retroactive-extraction design decouples capture correctness from detection latency. Headless-first retires the genuinely novel risk (bar-accurate video extraction) before any pixel is rendered.

## Downsides, honestly

**The seam was built at n=1.** LDP/1 was designed before any driver exists in code, from one imagined hardware consumer and one README. The rule of three was violated deliberately (`DRAFT-ldp1-driver-seam.md` argues why), but the risk is real: 0.1.0 pays protocol ceremony that a 200-line webcam script would not, and if the seam is wrong-shaped, the ceremony was rehearsal for the wrong play. The escape hatch (collapse to in-proc, re-derive the wire later) is written down, which is not the same as it being cheap.

**First-cycle capture is an aesthetic compromise baked into the foundation.** Live loopists build texture across overdub passes; the wall will systematically show sparse first passes — a performer tapping one hi-hat line while the audience hears the eventual stack. `DRAFT-ring-capture-semantics.md` defers compositing honestly, but "deferred" here means the headline aesthetic ships constrained, and no one has seen whether first-pass-only walls feel magical or thin.

**Milestone 1 produces zero visible magic.** A green integration test and a directory of correctly-cut clips is the least demoable artifact this project will ever produce. Renderer-first with canned clips would have something on a projector in days. The ordering chosen retires risk instead of generating motivation, and on a passion project that trade is not free.

**The renderer bet is the least-controllable layer and the least-tested claim.** Sixteen hardware-decoded `<video>` elements, transform-animated by anime.js, plus WebRTC preview, inside Chromium's compositor and GC — every one of those is a place the plan says "should be fine" with no measurement behind it. The GStreamer compositor alternative is more deterministic and was rejected for iteration speed and the anime.js affordance, i.e., partly on taste.

**x264-by-license has an aesthetic cost the license table doesn't show.** The handover mask hides encode latency by holding the live feed longer — which means slower encodes change what the audience sees (more *now*, less *take*). The exclusion is correct under the record; the cost is real and lands on stage, not in CI.

**Governance mass.** The corpus is ten files and zero code for a tool whose first milestone is one webcam and one YAML file. Defensible under org doctrine — the harness exists precisely because sessions forget — but a solo maintainer should notice the plan:code ratio is currently infinite.

**The headline use case is proven last.** The Hapax path — the reason the project exists — rests on F1–F6 and ships at 1.0.0. Correct risk ordering, and still: three releases of work stand between now and knowing whether the device cooperates.

## Alternatives rejected, and what reopens them

Hapax-direct first (reopens if LDP thrashes during 0.1.0) · GStreamer compositor (reopens on 0.2.0/0.3.0 soak failure) · record-forward capture (never; it loses take starts by construction) · renderer-first ordering (reopens only as a morale decision, and should be named as such) · SQLModel-backed registry (reopens if runtime CRUD on sessions ever matters; YAML-plus-index is sufficient and simpler now) · NVENC (exception ADR only).

## Confessions

Early drafts stated Hapax signal behavior with more confidence than the evidence class supported; the assumption tags exist because review forced them, not because the drafter volunteered them. The ShowRunner mapping was designed from its README — plausible, unverified against its actual cue model. Scope grew monotonically across drafting until the ambition ceiling arrived from the human; the drafter did not self-limit, which is itself an argument for the fences it was asked to write.

## Falsifiable predictions

1. LDP/1 gains or changes at least one required field before 1.0.0.
2. The ±1-frame duration assertion fails first on segment/GOP boundary rounding, not camera latency.
3. The 0.2.0–0.3.0 soak surfaces at least one Chromium decode or GC issue forcing pool or rendition changes.
4. F3 (record-pass echo) holds; F1 (PC timing) produces the surprise that forces config hints.
5. The ~32 s smoke scenario proves too short to catch ring-cleanup bugs; a long-soak scenario gets added to 0.1.0 in practice.

If most of these fail, the plan was better than its author believed; if most hold, the plan priced its own risks correctly. Either outcome is information.
