# Contributing

This is an index, not a restatement — each linked file is the actual
authority on its topic.

1. **Start at `AGENTS.md`.** Governance summary, one-time setup, and the
   base dev commands. If you're an AI coding agent, it's your required
   reading before a first commit or edit.
2. **`COOKBOOK.md`** walks through actually running the toolchain locally —
   lint, tests, and (if you have a camera) the real-hardware capture POC.
   Run it before opening a PR; it's what CI runs, minus the network-only
   steps.
3. **Decisions go through `governance/qm/adr/`**, not code comments or PR
   descriptions — see that directory's own `README.md` for the process
   (squash-before-ratification, one decision per record, numberless drafts).
   A PR that changes architecture without a corresponding `DRAFT-*.md` is
   incomplete, not just under-documented.
4. **`HANDOFF.md`** is the current cold-start state — what's proven, what's
   open, what's next. Read it before assuming you know where the project is;
   rewrite it if your change moves any of that.
5. **`STUBS.md`** is the only place `NotImplementedError`/`TODO` are allowed
   to live in `src/`. Adding one anywhere else, or adding a new one here
   without a corresponding roadmap change, fails CI's placeholder sweep.
6. **Commits: human-only contributorship applies.** No `Co-Authored-By:`
   trailer naming a model or an unmonitored address — see
   `governance/qm/records/DRAFT-human-only-contributorship.md`. Tool
   involvement is a `Tools:` note where the artifact calls for one (e.g.
   `perspectives/`), never a byline.
7. **PRs that pass CI** (`.github/workflows/ci.yml` + `adr-lint.yml`) still
   need a human to review the actual decision, not just the diff — the ADR
   discipline exists because "it passed CI" and "this was a good call" are
   different questions.
