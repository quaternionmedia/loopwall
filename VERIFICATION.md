# loopwall — Verification track (V-track)

| | |
|---|---|
| **Standing** | Parked. Gates **1.0.0 only**. Nothing in 0.1.0–0.3.0 depends on any item here; the CLI driver declares explicit state and touches no inference path. |
| **Version** | 0.0.2 · **Date** 2026-07-16 |

Load-bearing empirical claims in `PLAN.md` are tagged `(assumption → Fn)` and are assumptions until discharged here, with the physical Hapax, using a MIDI capture harness. A failed falsifier amends the plan by ADR, not by memory. Run these in parallel with 0.1.0–0.3.0 whenever the hardware and an hour coincide; all six must be discharged before 1.0.0 ships.

| # | Claim under test | Method | Falsifier consequence |
|---|---|---|---|
| F1 | Program Change is emitted on pattern change; timing relative to the bar boundary is usable | capture across pattern-switch workflows | pattern identity falls back to config hints |
| F2 | No record-state emission exists | exhaustive capture across record workflows (arm, punch, overdub, clear) | a discovered emission **simplifies** the trigger; adopt it |
| F3 | Live input echoes to track output during the record pass | capture while recording with normal monitoring routing | auto-trigger demoted; footswitch becomes primary |
| F4 | Clock jitter (USB vs DIN) sits within EMA tolerance; ramp behavior is trackable | jitter statistics from capture | smoothing constants retuned |
| F5 | Camera A/V offset is stable ±1 frame across a session | repeated `loopwall calibrate` runs | per-session recalibration added to the ops checklist |
| F6 | Cycle math (bars-since-Start mod pattern length) matches device polymeter behavior | scripted polymeter capture | window computation amended |

Capture harness: any MIDI-logging script producing timestamped JSONL is sufficient; store raw captures in `verification/` with a one-line disposition per Fn. Evidence beats recall — undated claims about device behavior do not discharge a falsifier.
