# AGENTS.md

This project is governed by the Quaternion Media constitution, vendored at
`governance/qm` (a submodule pinned to this project's `project/loopwall`
branch of that repo). If you are an AI coding agent opening this repo with
no other briefing, read this file fully before your first commit or edit.

## Before you do anything

1. Read `governance/qm/README.md` and `governance/qm/PRINCIPLES.md` in full
   — the namespaces/precedence rules and the charter. Both are short.
2. This project's own decision records live in `governance/qm/adr/`, as
   `ADR-NNNN` (numbered locally, at ratification) or `DRAFT-*.md` before
   ratification. A human ratifies; you draft.
3. **Human-only contributorship applies to every commit you make here** (see
   `governance/qm/records/DRAFT-human-only-contributorship.md`): do not add
   yourself, your model name, or any co-author trailer naming an unmonitored
   address (e.g. a vendor `noreply@` address) to any commit. If your default
   tooling normally appends a `Co-Authored-By:` trailer, suppress it for
   this repo. Tool involvement is disclosed as a `Tools:` note where the
   artifact calls for one, never as a byline.
4. Follow the drafting-session handoff contract in `governance/qm/adr/README.md`
   before writing or amending any record.
5. A QM record may be tightened by this project's own `governance/qm/adr/`,
   never relaxed — see `governance/qm/README.md`'s "Namespaces and
   precedence."
6. **Start here, before the constitution:** `HANDOFF.md` is this project's
   own cold-start packet — read it and the corpus it points to (`PLAN.md`,
   `ROADMAP.md`, `STUBS.md`, `VERIFICATION.md`) before writing any code.
   Chat history is treated as nonexistent; the corpus is the only sanctioned
   cross-session memory.

## One-time setup on a fresh clone (Windows)

`CLAUDE.md` and `.github/copilot-instructions.md` are real symlinks to this
file, not copies — POSIX checkouts resolve them with no setup. On Windows,
enable Developer Mode (Settings → For developers) and run `git config
core.symlinks true` once per clone, then `git checkout -- .` if the files
were already checked out before that. Skipping this doesn't break
anything — the files degrade to one-line pointers containing just the
target path — but it isn't the intended, tested experience; see the
IDE-integrated governance discovery record in `governance/qm/records/` for
what was actually verified.

<!-- Project-specific setup commands, test commands, and conventions belong
     below this line; this seed only carries the governance-discovery part. -->

## Project setup and conventions

See `COOKBOOK.md` for runnable recipes (clone, install, verify, real-camera
capture test) — this section is the terse summary, that file is the one to
actually follow and the one to update if a command here goes stale.

- **Clone:** this repo uses a git submodule — `git clone --recurse-submodules`,
  or `git submodule update --init` after a plain clone.
- **Toolchain:** `uv sync` (py3.12, deps pinned via `uv.lock`); everything
  runs through `uv run`. Don't add `--all-extras` unless you're specifically
  working on the `hapax` driver (1.0.0) — that extra needs a C toolchain to
  build `python-rtmidi` from source, which milestone 1 never touches.
- **System deps** for capture work: `ffmpeg`/`ffprobe` on `PATH`; `v4l2-utils`
  on the Linux production target.
- **Entry points (target shape, not all built yet):** `loopwall play
  <scenario> [--headless]` · `loopwall cli` · `loopwall replay <session>`
  land at 0.1.0 build order steps 1–5, 8–9 (`ROADMAP.md`); `loopwall serve`,
  `loopwall calibrate` at 0.2.0. Today, `uv run loopwall --version`/`--help`
  is what actually exists — don't assume the others work without checking
  `ROADMAP.md`'s build order first.
- **Tests:** `uv run pytest -m "not hardware"` day to day (coverage ratchet
  enforced in CI); hardware-marked tests are skipped in CI and run manually
  against real cameras/MIDI gear — see `COOKBOOK.md` recipe 3.
- **Lint:** `uv run ruff check .` and `uv run ruff format --check .`.
- **Placeholder discipline:** `NotImplementedError`/TODO markers are only
  permitted on paths listed in `STUBS.md` — see that file's own Standing row.
