# loopwall — Cookbook

| | |
|---|---|
| **Standing** | Practical, task-oriented; not part of the ratifiable corpus (contrast `PLAN.md`/`ROADMAP.md`) and not sanctioned session memory (contrast `HANDOFF.md`). Update it when a recipe here stops working — it should always reflect what a contributor can actually run today, not the target shape. |

Recipes for verifying a local checkout actually works, in increasing order
of what they touch. None of these play a show yet — `loopwall play
<scenario>` and friends are 0.1.0 build-order work (`ROADMAP.md`) and don't
exist yet; see "What's not here yet" at the bottom.

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

## 4. Run the demo: live preview, space to capture-and-loop

`loopwall demo` (PLAN.md §3.7) is the fastest way to see the whole point of
this project locally, in one command: a live-preview window shows the
camera feed from the start; press **space** and everything captured since
the last press (or since the start, the first time) becomes a clip that
opens in its own looping window — the ring keeps recording, uninterrupted,
into what becomes the next one. It bypasses LDP/core/the driver entirely —
not a preview of the eventual `loopwall play`, just the capture/extraction
mechanic made visible and playable.

```sh
uv run loopwall demo
```

Same safe-by-default `lavfi` source as `monitor` (needs `ffmpeg` on `PATH`,
same as recipes 3 and 5). Terminal shows the same live `monitor` dashboard
alongside demo status (recording elapsed time, clips captured, controls);
**space** to capture-and-loop, **'q'** to quit — no Enter needed, it's a
single-keypress poll, not a line prompt. Loop windows are left running when
you quit — they're the point, close them by hand when you're done admiring
your own looped performance.

Against a real camera, same flags as `monitor`:

```sh
# Windows
uv run loopwall demo --camera-type dshow --device "Your Camera Name"

# Linux
uv run loopwall demo --camera-type uvc --device /dev/video0
```

`--help` lists every option, including `--preview-port` (the local TCP port
the live-preview stream uses — change it if 17123 is taken). Verified end
to end against real hardware during development, two captures in one
session: real camera → live-preview window → space → looping clip window
(905KB, ~15s) → space again 4s later → a second looping clip window (205KB,
~4s) → 'q' → confirmed via `tasklist` that the capture process and the
live-preview process were both gone, while the two loop windows (by design)
kept looping.

**If the preview window is slow to appear**, that's expected, not stuck —
`demo` waits for it to actually finish binding before starting the ring
(HANDOFF.md's "CLI demo" note has the numbers), and a real camera's own
first-segment latency stacks on top of that. Give it several seconds
before assuming something's wrong; the "warming up" status line in the
terminal keeps updating throughout either way.

**One real limitation, named rather than hidden:** the swap from the live
preview to a looping clip is two independent `ffplay` windows, not one
continuous display — there's a visible cut opening the new window, not the
masked, seamless handover PLAN.md §3.5 describes for the real wall. That
needs the actual renderer (pre-warmed video elements, cross-fade), which is
0.2.0 scope; this demo doesn't attempt to fake it.

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

## 6. What's not here yet

- No `loopwall play <scenario>`, `loopwall cli`, or `loopwall replay` —
  those are 0.1.0 build order steps 1–5 and 8–9 (`ROADMAP.md`); today's CLI
  is `--version`/`--help`, `monitor` (Tier 0), and `demo` only — and `demo`
  bypasses LDP/core/the driver entirely (PLAN.md §3.7), it isn't a preview
  of `play`.
- `RingCapture` has a working `lavfi` path now (recipes 3/4's default, and
  `tests/integration/test_monitor_lifecycle.py` /
  `test_demo_lifecycle.py`) — but that only proves lifecycle (start, run,
  clean teardown), not extraction *accuracy*. The burned-timecode,
  window-assertion `lavfi` test build order steps 6–7 actually call for
  (asserting `capture/extract.py` cuts the right window, not just that it
  runs) doesn't exist yet — see `HANDOFF.md`.
- No V4L2/Linux run has actually happened yet against real hardware —
  recipe 5 has only been exercised via DirectShow on Windows. The
  `RingCapture`/`monitor`/`demo` `uvc` path is camera-agnostic by design,
  but that's a design claim, not yet a verified one on Linux.
- The `tests/integration/test_smoke.py` milestone-1 gate (`ROADMAP.md`'s
  "Milestone 1 integration gate (verbatim)") doesn't exist yet — recipe 5's
  test predates it and isn't a substitute.
