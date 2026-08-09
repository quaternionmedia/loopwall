# AGENTS.md

This project is governed by the Quaternion Media constitution, vendored at
`governance/qm` (a submodule pinned to this project's `project/loopwall`
branch of that repo). If you are an AI coding agent opening this repo with
no other briefing, read this file fully before your first commit or edit.

## Before you do anything

1. Read `governance/qm/README.md` and `governance/qm/PRINCIPLES.md` in full
   — the namespaces/precedence rules and the charter. Both are short.
2. This project's own decision records live in `governance/qm/adr/` — inside
   the submodule, on this project's own branch, not at this repo's root — as
   `ADR-NNNN` (numbered locally, at ratification) or `DRAFT-*.md` before
   ratification. A human ratifies; you draft.
3. **Everything you produce arrives as a pull request.** Work on a branch and
   open a PR for human review — in this repo, and in the `governance/qm`
   submodule when you touch this project's records there. Never commit to,
   merge into, or push a shared branch directly, and never merge your own
   work, however small or mechanical the change looks. If you cannot open a
   PR, hand the branch back rather than merging it.
4. **Human-only contributorship applies to every commit you make here** (see
   `governance/qm/records/DRAFT-human-only-contributorship.md`): do not add
   yourself, your model name, or any co-author trailer naming an unmonitored
   address (e.g. a vendor `noreply@` address) to any commit. If your default
   tooling normally appends a `Co-Authored-By:` trailer, suppress it for
   this repo. Tool involvement is disclosed as a `Tools:` note where the
   artifact calls for one, never as a byline.
5. Follow the drafting-session handoff contract in
   `governance/qm/adr/README.md` before writing or amending any record.
6. A QM record may be tightened by this project's own records, never
   relaxed — see `governance/qm/README.md`'s "Namespaces and precedence."
7. Banned in any pre-ratification `DRAFT-*.md` record: "previously",
   "originally", "earlier draft", "re-review", "renumber", "retroactive",
   "supersedes the ... (stance|finding)", "corrected". Drafts are rewritten
   in place, not narrated. The ADR lint enforces this over prose only, so
   quoting the list in a code span is fine.
8. **Start here, before the constitution:** `HANDOFF.md` is this project's
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
- **Toolchain:** `uv sync` (`.python-version` pins 3.12.10; deps pinned via
  `uv.lock`); everything runs through `uv run`. `--all-extras`/`--extra hapax`
  pulls in `mido`/`python-rtmidi` for MIDI support (recipe 4's `--midi` and,
  later, the `hapax` driver at 1.0.0) — installs from prebuilt wheels with no
  compiler needed on both Windows and Linux *because* of the 3.12 pin
  (`python-rtmidi`'s latest release doesn't ship wheels for newer CPython
  yet, on either platform — checked directly against PyPI); it's still not
  in the base `uv sync` since most of this cookbook doesn't need it.
- **System deps** for capture work: `ffmpeg`/`ffprobe` on `PATH` (both
  platforms); `v4l2-utils` on the Linux production target only. See
  `COOKBOOK.md`'s Platform notes section for what else differs.
- **Entry points, all built:** `loopwall play [<scenario>] [--headless]`
  (no `SCENARIO`: the live wall, PLAN.md §3.7; with one + `--config`:
  scripted replay through `Core`, `--monitor` for a live Tier 0+1 terminal
  view) · `loopwall monitor` (Tier 0 only, PLAN.md §3.6) · `loopwall
  midi-ports` · `loopwall replay <session_dir>`. `loopwall serve`,
  `loopwall calibrate` land at 0.2.0; the bare `loopwall cli` REPL (distinct
  from scripted `play <scenario>`, which already exists) is still open —
  check `ROADMAP.md`'s build order before assuming anything else works.
- **Tests:** `uv run pytest -m "not hardware"` day to day (coverage ratchet
  enforced in CI); hardware-marked tests are skipped in CI and run manually
  against real cameras/MIDI gear — see `COOKBOOK.md` recipe 5.
- **Lint:** `uv run ruff check .` and `uv run ruff format --check .`.
- **Placeholder discipline:** `NotImplementedError`/TODO markers are only
  permitted on paths listed in `STUBS.md` — see that file's own Standing row.
