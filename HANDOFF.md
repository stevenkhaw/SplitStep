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

## 2026-08-27 overnight update (all three loose ends closed)

1. **Wrapped-note burn: verified.** Steven restarted serve (00:58, after the
   wrap commit) and rendered `tiebreaker-full` numbered (01:09); the
   overnight session then extracted frames at 2.0 s / 180.9 s / 73.0 s and
   eyeballed them — counter pill correct on noted and unnoted clips, the
   112-char note wraps to three per-line pills, no clipping. Nothing to fix.
2. `tiebreaker-starred` was deleted outright (only `tiebreaker-full`
   remains) — the zero-seeded-notes question is moot.
3. The Phase-1 deferred follow-ups are folded into the Phase 2 plan as its
   Task 9 (`docs/superpowers/plans/2026-08-27-friend-mode-ui.md`).

Also overnight: **Gate 0 first-pass recorded** in
`docs/superpowers/plans/2026-08-27-gate0-fence-mount-first-look.md` — read
it before touching detection. Short version: `analyze_view` validated in
both directions (fence mount 0.128 → `pair`, high confidence; the 08-26
evening clip 0.0499 → `subject`, a knife-edge one part in five hundred
below the boundary), but the fence footage is same-side drills, not
cross-net play, and on a busy venue `both_present` saturates at 99.5%
(strangers inside the quad's top band) while the audio term measures the
venue again — every confidence sits in a 0.56–0.686 band. No tuning was
done. What moves it: a label-mode pass on 2026-08-25 source 01 (72
candidates, zero labels), and the quad-top-at-far-baseline re-detect
experiment the doc describes.

## The roadmap (per the distribution spec)

- **Gate 0: first-pass done (see above); human half remains.** Steven's
  items: label-mode pass on 2026-08-25 source 01, decide whether same-side
  drills count as "play", optionally run the quad-redraw experiment. Do NOT
  tune anything without reading
  `docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md` first.
- **Phase 2: plan written, awaiting Steven's review** —
  `docs/superpowers/plans/2026-08-27-friend-mode-ui.md` (9 tasks: mode flag
  end to end, dev-only shortcut tags, Advanced toggle, failed-job
  retry/dismiss + truthful empty-queue copy, first-run flow, wizard copy,
  library size, Phase-1 debt). Execute only after review.
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
--oneline -15`, then ask me what I want to tackle — likely reviewing the
Phase 2 plan (then executing it), or the Gate 0 human items above.
