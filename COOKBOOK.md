# loopwall — Cookbook

| | |
|---|---|
| **Standing** | Practical, task-oriented; not part of the ratifiable corpus (contrast `PLAN.md`/`ROADMAP.md`) and not sanctioned session memory (contrast `HANDOFF.md`). Update it when a recipe here stops working — it should always reflect what a contributor can actually run today, not the target shape. |

Recipes for verifying a local checkout actually works, in increasing order
of what they touch. The bare `loopwall cli` REPL doesn't exist yet (0.1.0
build-order work, `ROADMAP.md`) — everything else in this file does; see
"What's not here yet" at the bottom for what's still genuinely open.
Windows and Linux are both actively used with this codebase (this is a
Windows dev machine; Linux is `config.example.yaml`'s production target) —
see "Platform notes" right after the prerequisites below for what
genuinely differs between them, then the per-recipe `# Windows`/`# Linux`
command blocks for the specifics.

## 0. Prerequisites

- [uv](https://docs.astral.sh/uv/) — manages the Python version and venv, nothing to install by hand.
- `ffmpeg` and `ffprobe` on `PATH` — needed from recipe 3 onward (recipe 3's monitor spawns a real `ffmpeg` process even in its default synthetic-source mode); recipes 1–2 don't touch it at all.
  - Windows: `choco install ffmpeg` (or any build that puts `ffmpeg.exe`/`ffprobe.exe` on `PATH`).
  - Linux (production target — see `config.example.yaml`'s `uvc` camera type): `ffmpeg` package plus `v4l2-utils` for `v4l2-ctl`.
- A camera — only for recipe 5, or recipes 3/4 if you point `monitor`/`play` at real hardware instead of their default synthetic source. Any UVC webcam on Linux, any DirectShow-visible camera on Windows.
- A Playwright-installed Chromium — only for recipe 7 (`tests/browser/`); `uv run playwright install chromium`, see that recipe.

## Platform notes

Both platforms are real, actively-used targets for this codebase — not
"Windows is what's tested, Linux is aspirational." What differs:

**Windows** (this project's own primary dev environment so far):

- Camera type `dshow`; device names come from `ffmpeg -f dshow
  -list_devices true -i dummy` (recipe 5).
- `ffmpeg` from Chocolatey resolves to a launcher shim that spawns the
  real binary as a *child* process — killing only the tracked PID would
  orphan a live capture with the camera still open. Not something you
  need to do anything about (`RingCapture`/`proctree.py` already kill the
  whole process tree for exactly this reason), just why that code exists
  if you're reading it.
- A benign console-log quirk you may see on shutdown: `ConnectionResetError`
  from `_ProactorBasePipeTransport._call_connection_lost`, printed when the
  ring's `ffmpeg` process is forcibly killed mid-connection to the preview
  relay. Cosmetic — a Windows `ProactorEventLoop` artifact, already
  suppressed for the one case that matters (`WallDriver.start()`'s scoped
  exception handler) — not a sign anything actually failed.
- No tmpfs equivalent — the ring directory defaults to a plain temp dir.
  `config.example.yaml`'s `/dev/shm/loopwall` is a Linux path; don't copy
  it verbatim into a Windows config, point `capture.ring.dir` somewhere
  real instead (or just omit it and let `play`/`monitor` default it).

**Linux** (`config.example.yaml`'s production target):

- Camera type `uvc` (V4L2); device paths like `/dev/video0` (`v4l2-ctl
  --list-devices` to enumerate, recipe 5). Needs the `v4l2-utils` package.
- Point the ring directory at tmpfs for real use (`/dev/shm/loopwall`,
  `config.example.yaml`'s own default) — ring segments are short-lived and
  rewritten constantly, which is what tmpfs is for.
- MIDI (the `hapax` extra) needs ALSA's runtime library present
  (`libasound2` or your distro's equivalent) — normally already installed
  on any desktop Linux system with audio support. If it's missing, expect
  an import error at `mido`/`rtmidi` import time, not at `uv sync` time;
  not yet hit or verified directly in this project, noted here as the
  likely first thing to check if MIDI support fails to import on Linux.
- **Not yet verified**: no actual capture run has happened against real
  Linux/V4L2 hardware in this project yet (see "What's not here yet"
  below) — the `uvc` code path is camera-agnostic by design (same
  `RingCapture`, same ffmpeg-args translation as `dshow`/`lavfi`), but
  that's a design claim, not yet a confirmed one on real Linux hardware.

**Both platforms**: the `.python-version` pin to `3.12.10` matters
equally on either — `python-rtmidi`'s newest release doesn't ship
prebuilt wheels past Python 3.12 on *either* platform (checked directly
against PyPI's file listing for both), so skipping the pin risks the same
from-source-build failure regardless of OS, not just on Windows (see
recipe 1).

## 1. Clone and install

```sh
git clone --recurse-submodules git@github.com:quaternionmedia/loopwall.git
cd loopwall
uv sync
```

`--recurse-submodules` matters — `governance/qm` is a submodule (pinned to
this project's own `project/loopwall` branch), not a plain directory. If you
already cloned without it: `git submodule update --init`.

**Don't add `--all-extras`** to `uv sync` unless you actually want MIDI —
either recipe 4's `--midi`/`--midi-port` (external clock + an assignable
trigger for the wall) or, later, the `hapax` driver (1.0.0, `STUBS.md`).
The `hapax` extra pulls in `python-rtmidi`, which as of this writing only
ships prebuilt wheels through Python 3.12, on **both** Windows and Linux
(checked directly against PyPI's file listing for each) — on a newer
interpreter with no matching wheel, it falls back to building a native
extension from source via Meson, which needs a C toolchain (`gcc`/MSVC)
a plain dev setup usually doesn't have. That's why this repo's
`.python-version` pins `3.12.10` rather than tracking the newest available
interpreter — with it, `uv sync --extra hapax` installs from prebuilt
wheels, no compiler involved (confirmed on this project's own Windows dev
machine, which has no C toolchain at all and previously couldn't build it
under 3.13; see "Platform notes" above for the Linux-side ALSA runtime-lib
caveat, which is separate from this wheel-availability one). The wall
works fully without the extra either way (keyboard/browser control only),
so the base `uv sync` is all that's needed for anything in this cookbook
that isn't MIDI-specific.

## 2. Verify the toolchain (no camera needed)

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest -m "not hardware" -q
uv run loopwall --version
uv run loopwall --help
```

All five should succeed on a clean checkout. This is what CI runs (see
`.github/workflows/ci.yml`) minus the license-allowlist and gitleaks steps,
which need network/CI-only tooling. If `pytest` collects zero tests here,
something's wrong — there's always at least `tests/unit/test_package.py`.

## 3. Run the CLI monitor

`loopwall monitor` (PLAN.md §3.6) is a live, read-only terminal view of
capture-ring health — segment count, coverage window, disk usage. Tier 0
only, standalone: this specific command never shows transport/track state,
by design, not because that's still unbuilt — two processes can't open the
same camera, so a *separate* `monitor` process can never actually observe
a live driver's state. Tier 1 (transport + per-track state) exists, just
under a different command: recipe 6's `loopwall play <scenario> --monitor`,
which already owns both the ring and a live `Core` in one process.

**Safe by default — no camera touched:**

```sh
uv run loopwall monitor
```

This still spawns a real `ffmpeg` process (a synthetic `lavfi` testsrc, not
your camera) and needs `ffmpeg` on `PATH`, same as recipe 5. Ctrl-C to stop;
teardown is the same layered guarantee as the capture POC (`capture/ring.py`'s
`RingCapture` docstring) — verify with `tasklist`/`pgrep` as in recipe 5 if
you want to confirm it yourself.

**Against a real camera**, same device-discovery steps as recipe 5:

```sh
# Windows
uv run loopwall monitor --camera-type dshow --device "Your Camera Name"

# Linux
uv run loopwall monitor --camera-type uvc --device /dev/video0
```

`--help` lists every option (resolution, fps, ring size, refresh rate):

```sh
uv run loopwall monitor --help
```

## 4. Run live onboarding: `loopwall play`, no scenario

`loopwall play` with no `SCENARIO` argument (PLAN.md §3.7) is the fastest
way to see the whole point of this project locally: one browser window,
the wall. A live-preview tile is there from the start; **space** arms a
recording, which actually starts on the next tick (the pending tile shows
"armed" briefly, then "recording... Ns"); **space** again arms a stop,
which cuts the clip and adds it as a new, permanently looping tile with
its own duration and a **×** button to clear it — the grid grows with
every take, and the ring keeps recording, uninterrupted, into what
becomes the next one. Every capture goes through real LDP events into
`Core` — the same deterministic path a scripted `play <scenario>` run
uses (recipe 6), not a bypass. "Reset all" (bottom of the page) clears
every completed tile and cancels a pending arm, but leaves an in-progress
recording alone.

```sh
uv run loopwall play
```

This opens your default browser at `http://127.0.0.1:8000/wall` after a
moment (the terminal prints the URL too, in case it doesn't). Unlike
`monitor`, it defaults to a **real, auto-detected camera** — the whole
point is showing yourself, not a synthetic test pattern — falling back to
`lavfi` with a visible warning only if no camera can be found (needs
`ffmpeg` on `PATH`, same as recipes 3 and 5). Click the page once if
autoplay doesn't start the preview on its own (browsers gate that behind
a user gesture). Ctrl-C in the terminal stops everything.

Override the camera explicitly the same way `monitor` does:

```sh
# Windows
uv run loopwall play --camera-type dshow --device "Your Camera Name"

# Linux
uv run loopwall play --camera-type uvc --device /dev/video0

# Force the safe synthetic source (no hardware touched)
uv run loopwall play --camera-type lavfi
```

`--help` lists every option, including `--wall-port` (the browser page's
own port, 8000 by default), `--preview-port` (the local loopback TCP port
between the ring and the live-preview relay — recipe internals, not
something you normally need to touch) and `--session-root` (where the
session artifact — `log.jsonl` + clips + `index.json` — lands; defaults
to a temp dir). The session directory prints on exit.

**External MIDI clock/transport + an assignable trigger**, if you have a
controller or a DAW sending MIDI clock. This is on by default — `play`
auto-attempts the first available MIDI input the same way it
auto-detects a camera, no flag needed:

```sh
uv run loopwall play
# multiple MIDI devices attached? list them, then pin one explicitly:
uv run loopwall midi-ports
uv run loopwall play --midi-port "Your Controller Name"
# or turn it off entirely:
uv run loopwall play --no-midi
```

Needs `uv sync --extra hapax` first (`mido`/`python-rtmidi` — see recipe
1's note on why that's a separate extra, not the base install); without
it, `play` just runs without MIDI, no flag needed either way. When it
does connect, the terminal itself prints `MIDI connected: <port name>`
(stderr) right away — check that first if the wall doesn't seem to be
picking anything up, since with more than one MIDI-capable device
attached, auto-detect takes whichever `mido` lists first, which may not
be the physical controller you expect; `loopwall midi-ports` shows the
exact names `--midi-port` accepts. The wall page's own status line goes
further: the port name, whether a clock is currently pulsing plus its
bpm, and the current trigger binding. Click "Learn MIDI trigger" on the
page, then press a note or move a control on your device — that note/CC
(on that channel) becomes the start/stop trigger from then on, firing
through the exact same armed/next-tick path spacebar does. With a real
clock connected, that "next tick" is an actual musical bar boundary, not
the fast internal one — recordings quantize to it. If nothing's actually
there (no device, wrong `--midi-port` name, the extra genuinely isn't
installed), the wall still starts — keyboard/browser control always
works regardless — with a status line (and, for an explicitly-named
port that can't be reached, a terminal line too) explaining why MIDI
didn't connect.

**MIDI-triggered camera switching** binds a specific note/CC to switching
to a specific camera, alongside the shared start/stop trigger above —
still one active camera at a time, MIDI picks *which* one, not
simultaneous capture. Open the camera dropdown next to "Switch camera"
(populates on focus) and pick a target; each recent MIDI history chip
gets a small "→cam" button that binds it to whichever camera is currently
selected there. A trigger drives at most one thing — binding one to a
camera un-binds it from the toggle, and vice versa — and a camera has at
most one bound trigger, so rebinding either one silently replaces what
was there. The status line shows current bindings (`camera triggers:
lavfi:sim0 ← note 36 ch1`, for example).

**Other session management, all on the same page:**

- **Switch camera** — the dropdown next to "Switch camera" lists real
  detected devices (`GET /wall/cameras`, populated on focus, so nothing
  scans for hardware until you actually open it); picking one and
  clicking the button stops the current ring and starts a fresh one
  against the new device. Refuses while a take is actively recording
  (finish or stop it first).
- **Import a local file** — the file picker + "Import" button next to it
  adds a local video as a new, permanently looping tile, straight in
  alongside captured takes — no capture window, so no camera latency
  either. Refuses while recording, same reasoning as switching cameras.
- **New session / Load session** — "New session" (confirm-gated)
  finalizes the current session and starts a fresh one without
  restarting the process; the session dropdown next to "Load session"
  lists past sessions under this run's session root (`GET /wall/sessions`,
  also populated on focus) and *resumes recording into* whichever one you
  pick — not read-only playback. Both refuse while recording.
- **Export config** — "Export config" downloads the running camera/ring/
  session settings as YAML in `config.example.yaml`'s own shape, for
  reproducible scripted replay via recipe 6.
- **Render composite** — "Render composite" stacks every currently-visible
  tile into one grid video in the background (same packed layout the
  page's own JS uses for the grid itself); the button reflects render
  status and turns into a link once it's done. Refuses on an empty grid;
  not blocked by an active recording otherwise — it's a separate,
  CPU-contending background job, not a state conflict.

**If the preview tile is slow to appear**, that's expected, not stuck — a
real camera's own first-segment latency takes a moment. Give it a few
seconds before assuming something's wrong; the status line at the bottom
of the page keeps updating throughout either way.

**Live-preview latency was a real, measured problem** in an earlier
version of this recipe (routing the preview through a second ffmpeg
process that decoded and re-encoded it as MJPEG) — fixed by removing that
transcode entirely: the browser now decodes the ring's own h264 stream
directly via `mpegts.js` (vendored in `wall/`, not CDN-loaded — see
PLAN.md §3.7 for the full before/after). If it still feels laggy on your
hardware, that's worth reporting, not assuming is expected.

**One real limitation, named rather than hidden:** a new tile appearing
is a plain DOM insert, not the masked, seamless cross-fade handover
PLAN.md §3.5 describes for the real wall. That needs the actual quantized
renderer (pre-warmed elements, drift correction), which is 0.2.0 scope;
this doesn't attempt to fake it.

## 5. Verify capture against a real camera

This exercises `capture/ring.py` + `capture/extract.py` against real
hardware — the project's own genuinely novel risk (see the retrospective in
`perspectives/`): can a window be cut accurately out of a
continuously-recording ring, after the fact.

**Find your camera's device name:**

```sh
# Windows (DirectShow)
ffmpeg -f dshow -list_devices true -i dummy

# Linux (V4L2 — the production target)
v4l2-ctl --list-devices
```

`tests/integration/test_capture_poc.py` defaults to `"Camera Sensor
OV02C10"` (this dev machine's camera) via DirectShow. Override it:

```sh
# Windows
LOOPWALL_POC_DSHOW_DEVICE="Your Camera Name" uv run pytest -m hardware tests/integration/test_capture_poc.py -v -s
```

On Linux there's no `dshow`-equivalent wiring in the test yet — `RingCapture`
itself is camera-agnostic (it just takes raw `ffmpeg` input args), so a V4L2
run means passing V4L2 input args to `RingCapture` directly rather than
through this specific test today. That gap is real; see "What's not here
yet."

**Run it:**

```sh
uv run pytest -m hardware tests/integration/test_capture_poc.py -v -s
```

Expect ~20–30 seconds (it's letting real video accumulate in the ring, on
purpose — there's no way to shortcut that and still be testing the real
thing). Output looks like:

```
loop 1 (uncompensated): actual=3.900s delta=-100.0ms
loop 2 (compensated):   actual=4.100s delta=+100.0ms
PASSED
```

The exact deltas will vary — see `HANDOFF.md`'s "Duration compensator" note
for what's already known about this (a real, unexplained ~100ms-scale bias
that doesn't respond consistently to a linear correction; parked for later
investigation, not a sign your setup is broken as long as the test passes).

**Camera safety.** This test turns your camera on for its duration and
turns it off deterministically afterward — verified repeatedly during
development, including on test *failure*, not just success. If you want to
confirm it yourself instead of taking that on faith:

```sh
# Windows, immediately after the pytest command above
tasklist /FI "IMAGENAME eq ffmpeg.exe"
# should print "No tasks are running which match the specified criteria."

# Linux
pgrep -a ffmpeg
# should print nothing
```

If either shows a lingering `ffmpeg` process, that's a real bug — please
file it rather than just killing the process and moving on (see
`RingCapture`'s own docstring in `capture/ring.py` for the layered
teardown guarantee this would be violating, and why one layer alone —
e.g. killing only the tracked PID — was already found insufficient once).

## 6. Replay a scripted scenario through the real driver

`loopwall play <scenario>` with `--config` (PLAN.md §3.2-3.3) is the
`cli` driver's other half — not a live take, a scripted replay of a YAML
script (`scenarios/smoke.yaml`) through the same `Core` recipe 4 uses.
For testing/conformance, not onboarding.

```sh
uv run loopwall play scenarios/smoke.yaml --config config.example.yaml
```

`config.example.yaml` points at `uvc`/`/dev/video0` (the Linux production
target) — on Windows, copy it and change `capture.cameras[0]` to `type:
dshow, device: "Your Camera Name"` (or `type: lavfi` for no hardware; see
"Platform notes" above for what else differs between the two configs,
e.g. the ring directory). `scenarios/smoke.yaml` runs in real time at its
scripted 120bpm (~32s); the session directory prints on exit, containing
`log.jsonl` (every LDP event and committed op, true time order),
`index.json`, and the clips themselves.

Add `--monitor` for a live Tier 0+1 terminal view (capture health,
transport, per-track state, PLAN.md §3.6) while the scenario runs:

```sh
uv run loopwall play scenarios/smoke.yaml --config config.example.yaml --monitor
```

Afterward, verify the run's own determinism claim (PLAN.md §3.1: every
driver's LDP stream is replayable) against the session directory it
printed:

```sh
uv run loopwall replay <the session directory printed above>
# OK: N committed ops replayed identically.
```

This is also, verbatim, the shape of `tests/integration/test_smoke.py`'s
`lavfi` CI twin (the milestone-1 gate, `ROADMAP.md`) — see that test
module if you want the fully-scripted version with all of the gate's own
assertions, not just a manual run.

## 7. Tour the wall in a real browser, scripted (Playwright)

`tests/browser/` runs recipe 4's own walkthrough — load `/wall`, arm and
record a take, watch it become a looping tile, record a second, clear one,
reset all — through a real Chromium instance instead of your hands. Written
to double as documentation: each test's own docstring narrates one piece of
what a performer sees and does, not just what the API returns. See
`tests/browser/README.md` for the full picture, including why it's the bare
`playwright` package and not the `pytest-playwright` plugin (a transitive
license conflict — that README has the details).

One-time setup beyond the base `uv sync` (needs the actual Chromium binary,
not just the Python package):

```sh
uv run playwright install chromium
```

Then, same as any other test:

```sh
uv run pytest tests/browser -v
```

`lavfi` only, same as everything else in this file except recipe 5 — never
touches real capture hardware. Runs as part of the default `uv run pytest -m
"not hardware"` too, once Chromium is installed; skips cleanly (not fails)
if it isn't.

## 8. What's not here yet

- No bare `loopwall cli` REPL — scripted `play <scenario>` (recipe 6)
  covers the scenario-playback half of that build-order item; an
  interactive REPL distinct from both `play` modes is still open.
- `tests/integration/test_smoke.py` (the milestone-1 gate, `ROADMAP.md`'s
  "Milestone 1 integration gate (verbatim)") exists now, including a
  pixel-content check standing in for "burned-timecode windows"
  (PLAN.md §3.4) — each clip's first frame compared via SSIM against an
  independently-rendered reference frame at its expected ring-relative
  time, catching a wrong-window extraction that duration-alone couldn't.
  Its `lavfi` CI twin passes (verified repeatedly for stability). The
  `hardware`-marked real-webcam variant has now been run: 4 of its 5
  numbered assertions pass clean against a real dshow webcam; the
  extraction-latency one (≤3s) does not hold at real 1280x720 resolution
  (~3.5-3.7s observed) — a real, disclosed, not-yet-root-caused finding,
  see `HANDOFF.md`.
- No V4L2/Linux run has actually happened yet against real hardware —
  recipe 5 has only been exercised via DirectShow on Windows, and the
  milestone-1 gate above only via `lavfi`. The `RingCapture`/`monitor`/
  `play` `uvc` path is camera-agnostic by design (same code, different
  ffmpeg input args), but that's a design claim, not yet a verified one
  on real Linux hardware — see "Platform notes" above.
- Recipe 4's MIDI support (`src/loopwall/midi.py`) is unit-tested against
  fake message objects, plus (since the Python 3.12 pin in recipe 1) a
  real-connection check on a Windows dev machine: `MidiInput.start()`
  against a real port opens cleanly, the mido-callback-thread → asyncio
  bridge doesn't crash, and shutdown is clean. What's still open: nothing
  was actively *sending* clock or notes during that check, so bar-boundary
  detection against a real clock source and a learned trigger actually
  firing from a real note/CC press remain unverified on any platform — if
  you have a DAW or controller that can transmit, that's the part still
  worth exercising for real. Also entirely unverified on Linux specifically
  (both the ALSA runtime-lib question and a real connection) — everything
  MIDI-related here has only ever been exercised on Windows so far.
