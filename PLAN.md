# loopwall — Plan

| | |
|---|---|
| **Standing** | Ratification-track. Ratifying this document ratifies the defaults in §4 and authorizes `ROADMAP.md`. |
| **Version** | 0.0.2 |
| **Audience** | Any implementing session, human or AI, starting from zero context. This corpus plus `HANDOFF.md` are the only sanctioned memory. |
| **Date** | 2026-07-16 |

## 1. Objective

loopwall augments a live looping performance with a dynamically composed grid of video tiles — one per currently playing musical loop. The wall starts empty. When the performer records a loop, a camera take of that recording pass is extracted retroactively from a rolling buffer, cut to exactly one pattern cycle, and becomes that loop's tile. Tiles appear, drop out, and swap as the arrangement changes, with transitions quantized to the musical grid. While a loop is being recorded, its tile shows the live camera feed; at the cycle close, the performer's past self takes over the seat without a visible seam.

The arrangement source is a pluggable **driver** behind one protocol. Core is deterministic given the driver's event stream and contains no source-specific logic. Headless operation is first-class: core and capture run with zero renderer clients attached, which is how CI, integration tests, and capture-only rigs operate.

Tiles are silent video; loop audio is live from the instruments.

## 2. Governance position

*Seams on standard protocols:* the driver boundary (LDP/1, §3.1; ADR "LDP/1 is the sole driver boundary") is the seam; peripherals speak MIDI, V4L2, RTSP/WHEP, HTTP. *Build the seam, buy the engines:* loopwall builds LDP, the scheduler, and the wall core; it buys FFmpeg, MediaMTX, Chromium, anime.js, and the looper hardware. *House stack:* Pydantic, FastAPI, Click, pytest; renderer is vanilla ES modules with vendored anime.js; the CLI monitor (§3.6) renders with Rich (MIT) — terminal-only, no new network surface, no seam of its own since it reads state other components already own. *Open-license exclusion:* ADR "Dependency disposition" — x264 default encoder, NVENC excluded, Chromium not Chrome. *Decision-record discipline:* amendments to this plan are ADRs in `governance/qm/adr/` (a submodule pinned to this project's `project/loopwall` branch of the `qm` repo — see that directory's `README.md`), squash-clean. Harness bindings: A1 records lint, A2 handoff packet at every gate, B1 evidence tags resolved by `VERIFICATION.md`, B2 coverage ratchet, B3 placeholder sweep allowlisted by `STUBS.md`, C1 fence diffs per release, C2 stop-loss, D1 two-key, D2 secret scanning, E1 artifact-only review for ratification-track documents.

## 3. Architecture

```
┌ drivers (exactly one authoritative) ──────────────────────┐
│ cli    REPL / scenario playback · internal metronome       │
│ hapax  MIDI ingest · clock · liveness/trigger inference    │
│ showrunner  sr-plugin ──WS /driver (LDP/1 JSON)──►         │
└──────────────────────┬─────────────────────────────────────┘
                 LDP/1 events (in-proc or wire)
                       ▼
┌ core (driver-agnostic, deterministic) ─────────────────────┐
│ timeline · track registry · scene reconciler               │
│ quantize scheduler → grid ops                              │
│ capture service ◄─ capture_cue · session artifact          │
│ FastAPI: WS /wall /console /driver · clip serving          │
│ (headless: identical behavior, no clients attached)        │
└──────────────────────┬─────────────────────────────────────┘
                       ▼
  capture daemon: FFmpeg segment ring on tmpfs
  (+ MediaMTX WHEP preview when a wall is attached)
                       ▼
  Chromium kiosk: CSS Grid wall · anime.js · live rec-tile
  · seamless handover · drift correction  ─►  HDMI output
```

Single host; one monotonic clock timestamps driver events and video frames, which is what makes retroactive bar-accurate extraction possible. A CLI monitor (§3.6) reads this same state from a terminal, orthogonal to the diagram above — it is a second read-only view, not a fourth layer.

### 3.1 LDP/1 — the driver seam

A driver is the sole authority for arrangement truth. It connects in-process (Python `Protocol`) or over WebSocket at `/driver` (token-auth, single-authority lock; a second connection is rejected with the incumbent's identity). Handshake declares capabilities that gate core behavior:

```json
{"t":"hello","proto":"ldp/1","driver":"cli","caps":{
  "clock":"internal",        // external | internal | defer
  "explicit_state":true,     // core disables inference smoothing
  "capture_cues":true, "media_binding":false,
  "style":"event"}}          // event | scene
```

| Event | Payload | Semantics |
|---|---|---|
| `transport` | playing, bpm, bar, beat, phase | Clock owner only |
| `track` | track, state ∈ empty·recording·playing·silent, pattern? | Arrangement truth |
| `accent` | track, velocity | Immediate-path tile pulse |
| `capture_cue` | track, pattern, cycle_start_bar, bars, camera? | Invokes capture service |
| `clip_bind` | track, pattern, source (file · URL · whep://) | Driver-supplied media |
| `scene` | desired seats [{track, source, label}], quantize? | Declarative; reconciled |
| `config_hint` | names, pattern_bars, cameras | Metadata |

Clock authority: `external` — core follows the driver's transport. `internal` — the driver runs its own metronome. `defer` — core's metronome supplies the timeline for engines without a beat clock; `quantize: none` lets cue-driven walls fire transitions on receipt.

Every driver's LDP stream is logged to the session artifact as timestamped JSONL and is replayable; the conformance kit replays golden streams through core in CI, which is the mechanism keeping three drivers honest against one core.

### 3.2 Drivers

| | `cli` | `hapax` | `showrunner` |
|---|---|---|---|
| Clock | internal | external (MIDI clock, 24 PPQN) | defer / none |
| State | explicit | inferred | explicit (cues/scenes) |
| Capture | commands | auto + gesture + console | cue layers |
| Media | capture | camera capture | scenes mix takes, clips, feeds |
| Style | event | event | scene |

**cli.** Click REPL plus YAML scenario playback against its own metronome (`play`, `bpm 120`, `track 3 record 4`, `mute 2`). It is the reference LDP implementation, the development and CI driver, the integration-test harness, and the rehearsal tool.

**hapax.** Ingests via `mido`/`python-rtmidi`, multi-port. Transport from clock ticks: EMA-smoothed BPM; bar:beat:phase from tick count since Start, honoring Continue/SPP; freewheel with `clock_stale` on dropout. Because the device broadcasts neither mute nor record state `(assumption → F2)`, this driver owns two inference stacks, invisible to core: *liveness* (note activity + hold timer of `hold_bars`, hysteresis against sparse-loop strobe; Program Change supplies pattern identity `(assumption → F1)`) and *capture trigger* (first activity on an empty track marks the take; window = the full enclosing pattern cycle, cycle phase = bars-since-Start mod pattern length `(assumption → F6)`, retro-extracted; requires record-pass echo `(assumption → F3)`; footswitch on a reserved control channel is the deterministic override, console the safety). Capture semantics per ADR "Retroactive ring capture, first-cycle semantics".

**showrunner.** A thin plugin per ShowRunner's plugin model connects outbound to `/driver`, mapping cue layers to `scene` declarations and `capture_cue`. No beat clock assumed.

### 3.3 Core

**Timeline** publishes bar-boundary wall-clock timestamps (`bar → t_mono`), the lookup table extraction cuts against. **Track registry** holds per-track state as last declared by the driver; console overrides are masks applied after driver truth, never mutations of it, so replaying a driver log always reproduces the same underlying state. **Scene reconciler** diffs declared desired state against current and emits ops; idempotent. **Quantize scheduler** commits grid ops (`add`, `remove`, `swap`, `reflow`) at the configured boundary, dispatched ~80 ms ahead with an `applyAt` timestamp; accents bypass quantization; with no renderer attached, committed ops are written to the session log and nothing else. **Capture service** is driver-invocable only via `capture_cue`. **Session artifact**: one directory per show — LDP JSONL log, `trackNN_pP_takeK.mp4` clips, Pydantic-validated `index.json`. The directory is the show's complete, replayable archive; all takes are kept.

### 3.4 Capture and extraction

One FFmpeg process per camera writing 2 s closed-GOP H.264 segments onto a tmpfs ring (default 120 s ≈ 90 MB at 720p30/6 Mbps), with an RTSP push to MediaMTX added only when preview is in use. Exposure, white balance, and focus are locked via `v4l2-ctl` at session start. Extraction cuts `[t(bar_a)+offset, t(bar_b)+offset)` on **realized** bar timestamps — a tempo ramp during the take is captured correctly — selects covering segments, and re-encodes once with `x264 veryfast` to exact cycle duration, `+faststart`. Target trigger→registered latency ≤ 3 s; the handover mechanic masks longer. Per-camera `offset_ms` defaults to 0 and is measured by `loopwall calibrate` (MIDI-timestamped cue vs flash on camera; UVC pipelines commonly sit at 50–150 ms), accuracy target ±1 frame at 30 fps `(measured → F5)`. A `lavfi` synthetic camera (testsrc with burned timecode) is a first-class camera type, used by CI to assert extraction windows without hardware.

### 3.5 Renderer

Chromium kiosk, pool of 16 pre-created `<video>` elements (decoders warm, paused on last frame when unseated), CSS Grid, anime.js transitions and velocity-scaled accent pulses. Layout `packed`: cols = ⌈√N⌉, rows = ⌈N/cols⌉, last row centered; `fixed16` and a 3×3 `brady9` preset behind config. Seats are sticky — joiners take the lowest free seat; reflow animates only on grid-dimension changes; assignment is deterministic and property-tested. A recording tile shows the live WHEP feed (red border) and, when the clip registers, keeps the live feed through further whole cycles as needed, swapping to the recorded loop exactly on a cycle boundary — extraction latency is invisible by construction. Drift correction re-anchors each tile at bar boundaries (> 50 ms nudges rate ±2 %, > 250 ms seeks), absorbing tempo changes after capture. Preview latency (~150–300 ms WebRTC) is cosmetic; clips are cut on the shared timeline, so playback sync is exact.

**Degradation ladder** — no failure blanks the wall unannounced. A tile demotes one rung at a time: recorded loop → last-frame freeze → neutral slate; a recording tile whose extraction fails holds live preview and raises a console banner; transport stop freezes all tiles and dims 20 %; driver disconnect holds the last committed wall until reconnect (the single-authority lock admits the same driver identity back). Console force-live/force-dead masks remain available throughout.

Latency budget: accent path ≈ 25–45 ms; quantized ops are scheduled ahead, so perceived error is ~one frame; the number that matters most is calibration accuracy (±1 frame).

### 3.6 CLI monitor

Read-only terminal visibility for whoever is running the show, independent of whether a browser `/console` is open — complementary to it, not a replacement: `/console` also carries control (force-live/force-dead masks, §3.5); the CLI monitor only ever reads. Staged with what there is to monitor:

- **Tier 0** (no driver/core dependency — available as soon as the capture service exists): capture-ring health read directly from the capture service — segment count, ring coverage window, last extraction latency, ring disk usage. Useful standalone during camera setup and rehearsal, before any driver or track state exists to show.
- **Tier 1** (lands once `core/timeline`, `core/tracks`, and the `cli` driver are built): adds transport (bpm/bar/beat/playing) and per-track state (empty/recording/playing/silent), read from the same state the scheduler commits from — no separate state machine, so the monitor can never show something core didn't actually commit.

Rendered with Rich (MIT; terminal-only, no new network surface). Refresh is polling, not push — a monitor lagging by one refresh interval is an acceptable cost for a read-only view, unlike the accent path's `/wall` latency budget above.

## 4. Ratified defaults

Ratifying this plan ratifies these values. Amendments are ADRs.

| Parameter | Default | Revise when |
|---|---|---|
| Capture trigger (hapax) | auto first-activity · footswitch override · console safety | F3 fails → footswitch primary |
| Capture semantics | first-cycle · gesture-replace · keep all takes | a compositing design is ratified (ADR "Ring capture, first-cycle semantics") |
| Quantize / layout / seats | bar · packed · sticky | performer feedback from a dress run |
| Cameras / capture spec | single camera · 720p30 · ring 120 s | multi-station staging; tile size exceeds 540p |
| Extraction encoder | x264 veryfast (VAAPI-on-open-drivers optional) | exception ADR only (ADR "Dependency disposition") |
| Recording tile | live WHEP preview | never below the §3.5 ladder |
| Clock in cue mode | defer metronome; `quantize: none` allowed | — |
| External driver wire | loopwall hosts `/driver` · boot-config selection · no hot swap | clock-authority handoff design is ratified |
| Output | local HDMI; stream-out post-1.0 | — |
| Headless mode | first-class; identical core behavior | — |
| CLI monitor | Tier 0 (capture health) now; Tier 1 (transport/track state) once `core`+`cli` driver land | a design needs push rather than polling refresh |

## 5. Risks

| Risk | Mitigation |
|---|---|
| LDP shaped before any consumer exists in code | 0.1.0 exercises only the minimal subset; renegotiation window before 1.0.0 (ADR "LDP/1 is the sole driver boundary") |
| Trigger inference misfires (hapax) | footswitch authoritative; per-track auto flag; console re-capture; F3 |
| A/V offset drift | calibrate gate + soundcheck clap; F5 |
| x264 misses the ≤ 3 s target | handover masks arbitrary latency; measured at the 0.1.0 gate |
| Protocol drift across drivers | one Pydantic source for wire + in-proc; conformance replay in CI |
| Split-brain drivers / console | single-authority lock; overrides are masks (§3.3) |
| Camera auto-exposure shift mid-set | v4l2 locks at session start; ops checklist |
| tmpfs/disk exhaustion | ring deletion; session dir on SSD; console watchdog |
| Decode overload / GC / animation jank at 16 tiles | 720p source ≤ 540p effective; pooled elements; soak gates |
| Cue engines lack beat clock | defer metronome; `quantize: none` |

## 6. Out of scope

Overdub compositing; audio in tiles; hot driver swap; multi-driver composition; NDI; multi-display; CV/gate liveness; generative idle seats; publishing LDP as a standalone spec (revisit at a second external consumer).

---

**Ratification:** ☐ ratified as-is ☐ ratified with amendments (ADRs listed) ☐ revise and resubmit
