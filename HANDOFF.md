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

## Where things stand (2026-08-26, end of a long build day)

All merged to master and pushed to origin; 819 Python + 610 web tests green.
Landed today, in order: Phase 1 "server friend-readiness" (optional
`--library` via flag → `SPLITSTEP_LIBRARY` → config file; `serve --create`
requiring an explicit `--library`; bundle-first resource paths; routes for
import/detect/retry/inbox; job errors as sentence + `error_detail`;
per-library clip colour profile — migrations 010/011), clips grouped by
source on disk (`clips/NN/START-END.mp4`, one-time reconcile sweep),
numbered reels (migration 012; burned counter + note via a **PIL-rendered
PNG composited with ffmpeg `overlay` — NEVER `drawtext`, my ffmpeg build
lacks freetype**), per-video tabs on the session page, a Clips panel
(watch cut clips in full quality in-app, Reveal in Finder), `index.html`
served `no-cache` (a stale cached SPA cost us an evening — don't undo it),
and reel notes that inherit from rally notes.

The real library (`/Volumes/SanDisk_2TB/SplitStep`) is migrated through 012,
its 32 clips are swept into per-source folders, and a numbered points reel
has rendered and been verified frame-by-frame. Backup from before the
migrations: `library.db.bak-2026-08-26-pre-012` beside the live db.

## Loose ends to pick up first

1. **The wrapped-note burn has not been eyeballed yet.** I (Steven) still
   need to: restart `ss serve` (my alias; the running server may predate the
   wrap/pill/`i / total` burn code), hard-refresh, and hit the green Render
   on `tiebreaker-full` — its 18 coaching notes are seeded and waiting. When
   that render lands, extract a frame from a noted clip and *look at it*:
   this is the first render through the line-wrap path
   (`splitstep/media/numbered.py` — `_NOTE_SIZE`, `textwrap.wrap(width=48)`
   are the knobs if a long note looks wrong).
2. `tiebreaker-starred` got zero seeded notes (its spans' rallies carry
   none) — expected, but confirm I agree.
3. Deferred small follow-ups from the Phase-1 reviews are listed at the
   bottom of `.superpowers/sdd/progress-phase1-friend-readiness.md` — fold
   them into the next phase's plan rather than doing them loose.

## The roadmap (per the distribution spec)

- **Gate 0, still pending:** a fence-mounted clip sits in the real library's
  `_inbox`. Run it through the wizard, see what `pair` mode produces — the
  first real two-player footage the detector has ever seen. Record the
  verdict in `docs/superpowers/plans/`. Do NOT tune anything without reading
  `docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md` first.
- **Phase 2:** friend-mode UI (hide label mode + re-segment behind a
  setting; `mode: friend|dev` in the appconfig file), first-run flow,
  failed-job retry UI. Write its plan fresh against landed code.
- **Phase 3:** Tauri v2 shell + PyInstaller bundle + `.dmg`. The spec's
  Architecture section carries the contract (`resources.py` already resolves
  bundle-first; `--create` is first-run-only in the shell; the 503 no-UI
  page should branch on `resources.bundle_dir()` so a friend never reads
  "run npm").

## How this project is worked on (hard-won, don't relearn)

- Tests: `~/miniconda3/envs/splitstep/bin/python -m pytest -q` — the `-m`
  form ALWAYS (bare `pytest` in a worktree silently imports the wrong
  checkout). Full suite foreground in one call; never background it and
  poll. Web: `cd web && npx vitest run` — read the full summary line, a
  truncated tail once hid a failure; `npm run check`; `npm run build`
  (worktrees need `npm install` first).
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

Start by reading `CLAUDE.md` and the distribution spec, check `git log
--oneline -15`, then ask me what I want to tackle — likely the render
verification (loose end 1) or Gate 0.
