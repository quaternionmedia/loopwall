# loopwall — Cookbook

| | |
|---|---|
| **Standing** | Practical, task-oriented; not part of the ratifiable corpus (contrast `PLAN.md`/`ROADMAP.md`) and not sanctioned session memory (contrast `HANDOFF.md`). Update it when a recipe here stops working — it should always reflect what a contributor can actually run today, not the target shape. |

Recipes for verifying a local checkout actually works, in increasing order
of what they touch. `loopwall cli` and `loopwall replay <session>` don't
exist yet (0.1.0 build-order work, `ROADMAP.md`); see "What's not here yet"
at the bottom.

## 0. Prerequisites

- [uv](https://docs.astral.sh/uv/) — manages the Python version and venv, nothing to install by hand.
- `ffmpeg` and `ffprobe` on `PATH` — needed from recipe 3 onward (recipe 3's monitor spawns a real `ffmpeg` process even in its default synthetic-source mode); recipes 1–2 don't touch it at all.
  - Windows: `choco install ffmpeg` (or any build that puts `ffmpeg.exe`/`ffprobe.exe` on `PATH`).
  - Linux (production target — see `config.example.yaml`'s `uvc` camera type): `ffmpeg` package plus `v4l2-utils` for `v4l2-ctl`.
- A camera — only for recipe 5, or recipes 3/4 if you point the monitor/demo at real hardware instead of their default synthetic source. Any UVC webcam on Linux, any DirectShow-visible camera on Windows.

## 1. Clone and install

```sh
git clone --recurse-submodules git@github.com:quaternionmedia/loopwall.git
cd loopwall
uv sync
```

`--recurse-submodules` matters — `governance/qm` is a submodule (pinned to
this project's own `project/loopwall` branch), not a plain directory. If you
already cloned without it: `git submodule update --init`.

**Don't add `--all-extras`** to `uv sync` unless you're specifically working
on the `hapax` driver (1.0.0, `STUBS.md`) — the `hapax` extra pulls in
`python-rtmidi`, which builds a native extension from source on platforms
without a prebuilt wheel and needs a C toolchain (Meson + a working `gcc`/MSVC)
that a plain dev setup usually doesn't have. Milestone 1 (`ROADMAP.md`) never
touches this driver, so the base `uv sync` is all that's needed for anything
in this cookbook.

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
only today: no transport or track state yet, since `core`/the `cli` driver
don't exist (recipe 6 lists what that's blocked on).

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
the wall. A live-preview tile is there from the start; **space** starts
recording; **space** again cuts everything captured since then into a
clip and adds it as a new, permanently looping tile — the grid grows with
every take, and the ring keeps recording, uninterrupted, into what
becomes the next one. Every capture goes through real LDP events into
`Core` — the same deterministic path a scripted `play <scenario>` run
uses (recipe 6), not a bypass.

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
a user gesture); **space** to start/stop-and-loop. Ctrl-C in the terminal
stops everything.

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
dshow, device: "Your Camera Name"` (or `type: lavfi` for no hardware).
`scenarios/smoke.yaml` runs in real time at its scripted 120bpm (~32s);
the session directory prints on exit, containing `log.jsonl` (every LDP
event and committed op, true time order), `index.json`, and the clips
themselves.

## 7. What's not here yet

- No `loopwall cli` (bare REPL) or `loopwall replay` — those are 0.1.0
  build order steps 1–5 and 8–9 (`ROADMAP.md`); today's CLI is
  `--version`/`--help`, `monitor` (Tier 0), and `play` (recipes 4 and 6).
- `RingCapture` has a working `lavfi` path now (recipes 3/4's default, and
  `tests/integration/test_monitor_lifecycle.py` /
  `test_wall_app.py`) — but that only proves lifecycle
  (start, run, clean teardown), not extraction *accuracy*. The
  burned-timecode, window-assertion `lavfi` test build order steps 6–7
  actually call for (asserting `capture/extract.py` cuts the right
  window, not just that it runs) doesn't exist yet — see `HANDOFF.md`.
- No V4L2/Linux run has actually happened yet against real hardware —
  recipe 5 has only been exercised via DirectShow on Windows. The
  `RingCapture`/`monitor`/`play` `uvc` path is camera-agnostic by design,
  but that's a design claim, not yet a verified one on Linux.
- The `tests/integration/test_smoke.py` milestone-1 gate (`ROADMAP.md`'s
  "Milestone 1 integration gate (verbatim)") doesn't exist yet — recipe 5's
  test predates it and isn't a substitute; recipe 6 exercises the same
  command by hand but isn't a scripted assertion either.
