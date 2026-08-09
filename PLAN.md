# loopwall — Plan

| | |
|---|---|
| **Standing** | Ratification-track. Ratifying this document ratifies the defaults in §4 and authorizes `ROADMAP.md`. |
| **Version** | 0.0.2 |
| **Audience** | Any implementing session, human or AI, starting from zero context. This corpus plus `HANDOFF.md` are the only sanctioned memory. |
| **Date** | 2026-07-17 |

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

- **Tier 0** (`loopwall monitor`, no driver/core dependency — available as soon as the capture service exists): capture-ring health read directly from the capture service — segment count, ring coverage window, last extraction latency, ring disk usage. Useful standalone during camera setup and rehearsal, before any driver or track state exists to show.
- **Tier 1** (`loopwall play <scenario> --monitor`, once `core/timeline`, `core/tracks`, and the `cli` driver's scenario-playback path are built): adds transport (bpm/bar/beat/playing) and per-track state (empty/recording/playing/silent), read from the same state the scheduler commits from — no separate state machine, so the monitor can never show something core didn't actually commit. Deliberately not a standalone `loopwall monitor` mode: two processes can't open the same camera (§3.7's live-preview note; confirmed project-wide), so a *separate* monitor process can never actually see a live driver's state the way an earlier draft of this section assumed — `CliDriver` gained an `on_tick` hook instead, and the scripted `play` path (which already owns both the ring and a live `Core` in one process) is what renders it, once per bar. The no-`SCENARIO` interactive wall (§3.7) doesn't need this - its own browser status line already covers the same ground live, in more detail.

Rendered with Rich (MIT; terminal-only, no new network surface). Refresh is polling (per-bar for Tier 1, wall-clock interval for Tier 0), not push — a monitor lagging by one refresh interval is an acceptable cost for a read-only view, unlike the accent path's `/wall` latency budget above.

### 3.7 CLI interactive onboarding: the wall, in a browser

`loopwall play` with no `SCENARIO` argument — the `cli` driver's REPL half (§3.2; `CliDriver` in the same package is the scenario-playback half). One browser window (`/wall`, `server/app.py`) is the entire interface: a live-preview tile is on screen from the start; a toggle control starts and stops recording; each stop cuts a clip through real LDP events into `Core` — the same presence-based scheduler and session artifact any other driver gets, not a bypass — and adds it as a new, permanently looping tile. The grid grows with every take: this is a genuine first slice of the real wall §3.5 describes (a live-updating grid of tiles, driven by committed core state), built now rather than deferred whole to 0.2.0, on the explicit call that a terminal dashboard plus separate OS-level preview/loop windows was never going to be the permanent shape of this tool. MediaMTX/WHEP, quantized cross-fade transitions, and the `packed`/`fixed16`/`brady9` layout presets remain 0.2.0 scope (STUBS.md) — what exists now is real but simpler: one camera, one performer, a JS-computed `packed` layout (`cols = ceil(sqrt(N))`), no seat stickiness or reflow animation.

Bar/tempo are an internal quantization unit here when nothing external is driving them, not a musical concept — `Core`'s capture-cue mechanism needs *some* bar granularity to close a window on, so each server-side tick counts as one bar, with its nominal bpm computed from the *actual* elapsed time since the previous tick. That forces `core/timeline.py` to record an exactly-real-time breakpoint every tick rather than extrapolating from a fixed nominal rate — there's no fixed rate here to drift from in the first place, sidestepping the kind of nominal-vs-actual drift a real-hardware scripted-driver run surfaced under back-to-back captures (`HANDOFF.md`). Each take becomes its own track (`WallDriver` allocates a new track number per recording, not per-track re-takes) — the grid is built from simultaneously-playing tracks the same way the real wall's tile set is, just without seat reflow yet.

**Recording start/stop is quantized, always — not just when a MIDI clock is connected.** A toggle (spacebar, browser click, or a MIDI trigger) never acts immediately; it *arms* a start or stop, applied on the next bar tick from whichever clock is currently live (`WallDriver.toggle()`/`_apply_pending_and_tick()`). With the fast internal ticker that's only ever a fraction of a second - imperceptible, but the same code path a real external clock uses, not a separate "quantize only sometimes" special case. This is also what makes "armed" a real, wall-visible state (§3.6's own vocabulary: play/pause/armed/looping all need to be seen, not inferred) rather than a label with nothing behind it.

**External MIDI clock/transport** (`src/loopwall/midi.py`) replaces the internal ticker as the bar-boundary source whenever it's actively pulsing: `ClockTracker` counts standard MIDI clock pulses (0xF8, 24 per quarter note) into beats and bars against a configurable time signature, tracks a smoothed bpm, and reacts to Start/Continue/Stop (0xFA/0xFB/0xFC) - `WallDriver._run_ticker()` steps aside (`ClockTracker.is_active()`) while a real clock is live rather than racing it, and falls back to the internal ticker the moment it goes quiet (no pulses for 2s). `pulse()`/`is_active()`/trigger-matching are pure, no I/O - fully unit-tested without touching a real MIDI port or even having `mido` importable.

**An assignable start/stop trigger** (`MidiTrigger`, "MIDI-learn" style): pressing "Learn MIDI trigger" on the wall page arms `MidiInput.learning`; the next note-on or control-change message received becomes the new binding (channel + note/CC number), and every subsequent matching message calls the exact same `WallDriver.toggle()` a spacebar press or browser click does - one shared arm/apply path for every control surface, not a MIDI-specific parallel one. `mido`/`python-rtmidi` are optional (the existing `hapax` extra, `pyproject.toml`) - `python-rtmidi`'s prebuilt wheels lag the newest CPython by roughly a year, so a from-source build (needing a C toolchain not every machine has) only bites on a too-new interpreter; this project's own `.python-version` pins a wheel-covered release for exactly that reason. Either way MIDI is soft-imported, and a requested-but-unavailable port degrades to a clear status message rather than refusing to start the wall at all.

**Clear and reset** are plain LDP, not a special case: clearing one tile (`WallDriver.clear(track)`) emits `TrackEvent(state="empty")` for that track - the scheduler's existing presence-flip rule (§3.3) fires a `remove` op exactly as it would for any other track leaving the wall - and drops the tile from the grid; "reset all" does the same for every completed tile and cancels any pending arm, but deliberately leaves an in-progress recording running (a panic button for the wall, not for whatever's mid-take). A tile's on-screen label (`WallDriver.set_label`) and grid position (`move_tile`) are the one piece of state Core never sees at all - pure presentation, no LDP event, since neither is arrangement truth.

**"New session"** (`WallDriver.new_session()`) finalizes the current session artifact and starts a fresh one - new `Timeline`/`TrackRegistry`/`Scheduler`/`SessionArtifact`, same camera/ring/relay, never reopened - without restarting the process. Refuses (409, surfaced to the browser) while a take is actively recording, for the same reason reset doesn't touch one: there's no clean answer for which session a half-finished clip belongs to. Clips are served at `/clips/{session_id}/{filename}`, not just `/clips/{filename}` - track/take numbering restarts every session, so a bare filename would collide across sessions, and a past session's clips stay reachable this way even after a newer one starts (nothing is ever deleted).

**"Load session"** (`WallDriver.load_session()`) goes further than viewing a past session - it *resumes recording into it*: `SessionArtifact.load()` reopens the existing directory (its `index.json`, its still-being-appended `log.jsonl`) instead of creating a fresh one, rebuilds `self.tiles` from what it already has, and continues track numbering from the highest resumed track rather than restarting at 1. The fresh `Core` this builds around it has a `TrackRegistry` that has never seen those tracks, though - an unseen track defaults to `"empty"` (`core/tracks.py`), so `load_session()` re-dispatches `TrackEvent(state="playing")` for every resumed tile before returning, seeding the registry so a later `clear()` on one still fires a real presence-flip `remove` op instead of a silent no-op.

**A live camera switch** (`WallDriver.switch_camera()`) stops the current `RingCapture` and starts a new one against a different device, at a fresh ring directory (never the original, so the new camera's segment list is never mixed with a previous camera's leftovers) - `PreviewRelay` needs no changes, since it already tolerates a fresh inbound connection rather than assuming one ring's lifetime. Refuses while recording, same as `new_session`. `GET /wall/cameras` lists real devices for the browser's picker (`monitor/camera.py`'s `list_camera_devices`); content is host-hardware-dependent, so nothing about it is assumed at 0.0.x. The requested device is probed first, with a throwaway, non-tee'd `RingCapture` (`capture/ring.py`'s `wait_until_ready()`/`tail_stderr()`) - found necessary against real second-camera hardware, not hypothetical: a device that fails to open (wrong resolution/framerate for that device, already held open elsewhere) makes ffmpeg exit fast, and without checking, the switch swapped a broken ring in unconditionally and reported success. Only once the probe confirms the device actually opens does the working camera get stopped - a failed switch now leaves it running and raises a real, readable error instead.

**A stopped take's extraction is still synchronous (§3.3's own "capture_cue completion is synchronous" holds), but the wall no longer goes silent while it runs.** `WallDriver.state()`'s `extracting_track` is set the instant a stop is applied and cleared once the resulting tile lands - real-resolution extraction measured ~3.5s+ this session, long enough that a performer pressing record again during that window (seeing nothing to say otherwise) had their press silently queue behind the same lock rather than do anything, surfacing several seconds later in a way that looked like the wrong tile had started recording. The wall page shows a distinct "processing" tile for that window instead - a canvas snapshot of the live preview's current frame at the instant of the stop, not a generic spinner - and disables the record control entirely rather than let a press queue silently.

**MIDI-triggered camera switching** (`MidiInput.camera_triggers`, `WallDriver.bind_camera_trigger()`) extends the trigger system beyond the single shared toggle: a trigger picked from `.history` (the same "use something I already just played" pattern `select_midi_trigger` already uses, not a second learn-mode) can instead be bound to switch to one specific camera on match, dispatched through the exact same `switch_camera()` path a browser click uses - refusals (mid-recording) are swallowed at the MIDI callsite rather than surfaced, since there's no browser control in the loop to show them to. Exclusive on both sides: a trigger drives at most one thing (binding it to a camera un-binds it from the toggle, and vice versa) and a camera has at most one bound trigger, so rebinding either one silently replaces what was there. Deliberately still one active camera at a time - MIDI switches *which* one, not simultaneous multi-camera capture, consistent with §4's ratified single-camera default below.

**Local-file import** (`WallDriver.import_clip()`) adds a pre-recorded video as a new, permanently looping tile alongside captured takes - straight to `"playing"`, no `recording`/`capture_cue` LDP events, since there's no capture window at all. **MIDI trigger history** (`MidiInput.history`, bounded, most-recent-last) lets a performer pick a just-played note/CC directly instead of always re-entering "Learn" mode. **Config export** (`GET /wall/config.yaml`) downloads the running camera/ring/session settings in `config.example.yaml`'s own shape, for reproducible scripted replay. **A basic composite render** (`WallDriver.start_render()`, `capture/composite.py`) stacks every currently-visible tile into one video in the background - the same packed grid layout the page's own JS computes, each clip looped to the longest one's duration via the `loop` filter operating on already-decoded frames, not `-stream_loop` re-seeking the raw container - a real fix, not a hypothetical one: an early `-stream_loop`-based version broke in the field against a real imported clip with an audio track and an edit list (`tests/integration/test_composite.py` carries the regression case).

**Live-preview latency was a real, measured problem, not a nice-to-have fix.** The first version routed the ring's tee'd h264 preview stream through a second ffmpeg process that decoded it and re-encoded it as MJPEG for a plain `<img>` tag — workable, but slow enough to be "almost not usable" in your own words: a full decode+re-encode round trip stacked on top of libx264's own default lookahead/B-frame buffering (multiple hundreds of ms to over a second on its own). Fixed at both ends, not patched at one: `RingCapture`'s shared encoder now carries `-tune zerolatency` (capture/ring.py), and the second ffmpeg process is gone entirely — `server/relay.py`'s `PreviewRelay` is a plain asyncio TCP server that forwards the ring's raw h264/MPEG-TS bytes untouched, decoded in-browser by `mpegts.js` (vendored, not CDN-loaded — a live-performance tool can't depend on internet reachability at showtime) via `<video>` + Media Source Extensions, with `enableStashBuffer: false` and `liveBufferLatencyChasing: true` set specifically to keep the player itself from adding buffering back. No transcoding happens server-side for the live tile at all now.

The live preview still shares the ring's single capture process rather than opening the camera again — most camera devices/drivers refuse a second concurrent open (confirmed on this project's own dev hardware) — so `RingCapture` tees the encoded stream (one encode, two sinks: the ring, and the loopback TCP port `PreviewRelay` listens on) exactly as before. TCP, not UDP — UDP corrupted every frame with decode errors (packet loss/reordering, confirmed even on loopback) — and the relay listens while the ring's ffmpeg connects out as the client, never the reverse: a listening ffmpeg blocked the *entire* tee output, ring included, until a viewer connected, which would make ring reliability depend on a preview existing. The tee's preview slave also carries a bounded connect timeout and `onfail=ignore`, so a viewer that's slow to start, never connects, or disconnects mid-session costs the preview, never the ring. `WallDriver.start()` awaits the relay's own `asyncio.start_server()` completing before starting the ring - readiness is a real completion signal now, not netstat/ss polling against an external process.

Control moved into the page itself: a keyboard listener posts to `/wall/control` on spacebar, a click-to-record button and a clickable pending tile reach the identical `toggle()` path for mouse/touch-only operation (`clear`/`reset`/`midi_learn`/`new_session`/`label`/`move` are the other actions), `/wall/state` is polled every 200ms to keep the grid and status line current, and captured clips are served from the session directory via a session-scoped route (above). The pending tile carries four distinct visual states - ready, armed-to-start, recording (with a live elapsed-seconds counter), armed-to-stop - and every completed tile shows its own clip length, an editable label, move-left/move-right buttons, and a clear (×) button; the status line shows session identity (id, directory, created-at), MIDI connection/port/clock/trigger state, a "Learn MIDI trigger" control when MIDI is configured at all, and "New session". Onboarding defaults to a real, auto-detected camera (`monitor.camera.detect_default_camera` — first DirectShow video device on Windows, `/dev/video0` on Linux if present), falling back to a synthetic `lavfi` source with a visible warning only when nothing real is found — the opposite default from `monitor` and scripted `play`, which stay `lavfi` so those read-only/scripted paths never touch hardware by accident.

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
| CLI monitor | Tier 0 (`loopwall monitor`, capture health); Tier 1 (`loopwall play <scenario> --monitor`, transport/track state) | a design needs push rather than polling refresh |

## 5. Risks

| Risk | Mitigation |
|---|---|
| LDP shaped before any consumer exists in code | 0.1.0 exercises only the minimal subset; renegotiation window before 1.0.0 (ADR "LDP/1 is the sole driver boundary") |
| Trigger inference misfires (hapax) | footswitch authoritative; per-track auto flag; console re-capture; F3 |
| A/V offset drift | calibrate gate + soundcheck clap; F5 |
| x264 misses the ≤ 3 s target | handover masks arbitrary latency; measured at the 0.1.0 gate — **materialized**: real 1280x720 extraction runs ~3.5-3.7s, not yet root-caused, see `HANDOFF.md` |
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
