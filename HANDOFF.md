# SplitStep — session handoff

Copy everything below into a new chat.

---

I'm working on **SplitStep**, a local tennis video tool at
`/Users/stevenkhaw/Documents/GitHub/SplitStep`. Phone footage goes into a
library on my external drive, rallies get auto-cut (recall-biased — review
fixes the rest), I review them in a keyboard-driven queue, write coaching
notes, export 4K clips, and compile reels — which can render with a burned-in
"3 / 25" counter and my per-clip note. Read `CLAUDE.md` first: it is current
and load-bearing (pipeline truth, design tokens, the colour-profile rules,
the frontend lib/-only-logic rule). The end goal is a downloadable Mac app a
non-technical friend can use — full spec and phasing in
`docs/superpowers/specs/2026-08-26-mac-app-distribution-design.md`.

## Where things stand (2026-08-27, end of the distribution work)

**SplitStep is a Mac app and it has been installed from a download on a
second machine.** All four phases of the distribution plan are merged and
pushed: server friend-readiness, friend-mode UI, the Tauri shell + `.dmg`, and
numbered reels. `CLAUDE.md` now documents the desktop tier — read its
"The desktop app" section before touching `src-tauri/` or `packaging/`.

Build a `.dmg` with `./packaging/build_app.sh` (~12 min, ~10 GB free needed).
Install instructions for a non-technical user are `docs/INSTALL.md`; what has
actually been exercised by a human is `docs/SMOKE.md`.

**Nothing in the app is known-broken.** What remains is judgement work:

1. **Gate 0 — the real open question.** 2026-08-25 source 01 still has 72
   candidate rallies and **zero labels**. Until that pass exists,
   `splitstep labels score` cannot measure whether detection is good; Steven's
   read is that it works well in practice, and the corpus can neither confirm
   nor contradict that yet. Also open: whether same-side drills count as
   "play", and the quad-redraw experiment. Read
   `docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md` before
   touching a tuning constant.
2. **Two paths a human has never exercised**: creating a library through the
   chooser's **Create** button (the migrations bug lived exactly there), and
   the detect-finished notification, which has never fired on real footage.
3. **Numbered renders changed typeface.** Both the app and the CLI now burn
   Roboto Condensed Bold from `splitstep/assets/font.ttf`. Previously the CLI
   used macOS Arial Bold and only the frozen app used a bundled face, so the
   2026-08-26 frame-by-frame verification described one path and not the
   other. Worth one render to eyeball; narrower at the same size.

## What Phase 3 cost, and why it is worth reading

Four bugs reached a real install while every automated check passed. They
share one shape — **what was checked was what had been changed, not what would
run**:

| bug | why every check missed it |
|---|---|
| bundle unsigned (linker signature only, no sealed resources) | unquarantined macOS is lenient, so it ran locally and looked finished; a real download said "damaged", which has no way through |
| freeze carried zero migrations | the health check `/api/config` never opens the database |
| `back_to_chooser` denied by Tauri's ACL | every test stayed on the launcher, which is a local origin |
| a `.dmg` built from a binary older than the fix in it | the source was verified instead of the artifact |

Three now fail the build (`build_app.sh` guards: migration count, overlay font
present, app newer than its sources), the fourth is a comment in
`src-tauri/build.rs` tying `COMMANDS` to `generate_handler!`. `docs/INSTALL.md`
was also wrong in a way that would have stranded the friend: macOS 15 removed
the right-click → Open bypass.

## How this project is worked on (hard-won, don't relearn)

- Tests: `~/miniconda3/envs/splitstep/bin/python -m pytest -q` — the `-m`
  form ALWAYS (bare `pytest` in a worktree silently imports the wrong
  checkout). Full suite foreground in one call; never background it and
  poll. Web: `cd web && npx vitest run` — read the full summary line, a
  truncated tail once hid a failure; `npm run check`; `npm run build`
  (worktrees need `npm install` first).
- The Mac app: `./packaging/build_app.sh` is the only supported path to a
  `.dmg`, `cargo` is at `~/.cargo/bin` (not on PATH), and bundling wants
  ~2.5 GB free beyond the output or `bundle_dmg.sh` fails obscurely. Verify a
  change landed by mounting the `.dmg`, not by reading the source.
- Live library changes: sqlite `.backup` first, restart serve, verify
  counts. Never two serve processes on one `library.db`.
- The colour profile, the span-derived clip paths, and `rally_labels` are
  the most guarded machinery — read the rationale comments before touching,
  and never reintroduce `drawtext` or trust ffmpeg's autorotate.
- Two note systems on purpose: rally notes (coaching log, written during
  review) and reel item notes (per-reel caption, seeded from the rally note
  at add time, independently editable). Renders burn the reel item note.
- My workflow: feature branch → fast-forward merge to master, no PRs; other
  agents commit to master, so check `git log` before assuming the snapshot
  is current; push master + branches to origin as backup after real work.
- UI: design tokens only (`web/src/app.css`), `font-data` for every count
  and timecode, logic in `web/src/lib/` with tests, components thin,
  no blur-commits (the note editor commits on switching fields instead).
  Primary action per page = one filled accent button; inline text actions
  are `text-accent`, never gray.

Start by reading `CLAUDE.md` — it now covers the desktop tier too — then
`git log --oneline -15`, then ask me what I want. The build phases are done;
what is left is Gate 0 (the label pass on 2026-08-25 source 01) and whatever
using the app for real turns up.
