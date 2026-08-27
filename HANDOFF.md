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

## Phase 2 is done — built, reviewed, fixed, merged (2026-08-27, overnight)

The friend-mode UI shipped as 12 commits on `worktree-phase2-friend-mode`,
fast-forwarded onto master and pushed. **837 Python + 628 web tests green,
ruff clean, svelte-check 0 errors, vite build clean** — all five run after
the last edit, in the worktree, before the merge.

All nine planned tasks landed: the mode flag end to end (`GET`/`POST
/api/config/mode` over `~/Library/Application Support/splitstep/config.json`,
mirrored client-side by `lib/appmode.svelte.ts`), dev-only shortcut tags,
the Advanced toggle hiding the tuning tools, failed-job retry/dismiss,
truthful empty-queue copy, a first-run flow replacing the inbox-path empty
state, wizard copy explaining why the quad matters, library size on the
sessions page, and the Phase-1 debt fold-in.

It was then code-reviewed across ten angles and the findings applied as one
commit, `fix: apply the overnight review's findings`. The two worth knowing:
the sessions page had **two** effects fetching sessions, the second reading
`sessions.length` while its own callback reassigned it — a self-retriggering
loop hammering `/api/sessions`; and a dropped file that missed FirstRun's
target navigated the whole tab away to the video. Both fixed, along with
atomic config writes, a 503 (not a 500) from `/api/library/stats` on an
ejected drive, module-scope stores for job dismissals and the library size
(both were per-instance and reset on every navigation), and QuadEditor
offering a **"Run detection now"** button instead of telling a friend to go
type `splitstep detect` in a terminal.

Findings deliberately **not** taken, so nobody re-files them: the
MODES/Literal/TS-union triplication (the guard is defense in depth), `retry()`
swallowing network errors (the next poll shows the truth), JobsBadge's
always-on 1 s clock (pre-existing pattern), and QuadEditor staying visible in
friend mode (court assignment is core flow, and the detect button makes it a
complete path rather than a dead end). One real gap was logged rather than
fixed: **the Session page does not poll while a source is `detecting`** — it
is pre-existing, and belongs in the Phase 3 plan.

Nothing here has been driven by hand in the browser beyond the reviewer's own
checks — that is the first morning item below.

## Phase 3 shipped: there is a `.dmg` (2026-08-27, overnight)

**SplitStep is a Mac app now.** Tauri v2 shell, PyInstaller sidecar, unsigned
arm64 `.dmg`. Design in
`docs/superpowers/specs/2026-08-27-phase3-tauri-shell-design.md`, plan in
`docs/superpowers/plans/2026-08-27-phase3-tauri-shell.md`, install
instructions for your friend in `docs/INSTALL.md`.

Decided with you in four rounds of questions, so the reasoning is not lost:
arm64 only, unsigned with the Gatekeeper dance documented, ffmpeg from
evermeet.cx, icon generated from the design tokens, and the `.dmg` handed
over as a file rather than hosted.

**The shape that changed:** the library chooser is not a native dialog, it is
a **front layer** — a real screen, rendered by Tauri from its own bundle,
before any Python exists. It has to be: `Library.open()` refuses without a
`library.db`, so there is no server to serve a page asking which library the
server should open. It is skipped entirely on a normal launch; you see it on
a first run, when the drive is missing, or when you ask for it from
Settings → Change library. Switching re-points and never moves anything.

**A Rust port was raised and rejected**, and the reasoning is written into the
spec so it does not get relitigated: Tauri *is* a webview, so porting means
abandoning Tauri too, discarding ~20k lines of tested frontend including
VideoDeck's cross-source seeking, and keeping Python regardless.

Three findings from building it, all fixed:

1. **The orphan.** Force-quit or crash the shell and its Python child keeps
   running, holding `library.db` — the single-instance plugin guards the app,
   not the server, so the next launch would spawn a *second* worker against
   one database. Reproduced, then fixed with a pid file reaped on next launch
   (checked against the process name first, because pids are recycled).
2. **`--library` is a top-level flag, not a `serve` flag**, so the frozen
   entry point has to reorder argv before injecting the subcommand.
3. **The bundled font had to be instanced to Bold.** Google Fonts ships
   Roboto Condensed only as a variable font now, and `media/numbered.py`
   selects no variation — a variable file would silently render Regular and
   lighten a burn you verified frame-by-frame at Bold.

Suites: 846 pytest, 650 vitest, 8 cargo tests, ruff clean, svelte-check 0.
The evermeet ffmpeg is **9.0.1** — the same version the concat demuxer's
measured misbehaviour was characterised against, so the media layer's guards
still describe the ffmpeg that ships.

## The roadmap (per the distribution spec)

- **Gate 0: first-pass done (see above); human half remains.** Steven's
  items: label-mode pass on 2026-08-25 source 01, decide whether same-side
  drills count as "play", optionally run the quad-redraw experiment. Do NOT
  tune anything without reading
  `docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md` first.
- **Phase 2: shipped and merged** (see the section above) —
  `docs/superpowers/plans/2026-08-27-friend-mode-ui.md` for what each of the
  9 tasks was meant to do. Unreviewed in the running UI.
- **Phase 3: built.** See the section above. What remains is yours: run the
  clean-account smoke checklist, because a second macOS user account is the
  only honest test of a Gatekeeper flow and a first run.

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
--oneline -15`, then ask me which of these three I want:

1. **Look at Phase 2 in the UI.** `npm run build` then `splitstep serve`, and
   flip friend/dev with the Settings popover on the sessions page. Worth
   pushing on: the first-run flow with a real drop, a failed job's retry and
   dismiss, and QuadEditor's new "Run detection now" button (it confirms
   first — it costs manual edits and a full detect).
2. **The Gate 0 human items** in
   `docs/superpowers/plans/2026-08-27-gate0-fence-mount-first-look.md`: a
   label-mode pass on 2026-08-25 source 01 (72 candidates, zero labels),
   the decision on whether same-side drills count as "play", and the
   quad-top-at-far-baseline redraw experiment. Read
   `docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md` before
   tuning anything.
3. **Phase 3: Tauri v2 shell + PyInstaller bundle + `.dmg`** — the next build
   phase, and the one that makes the app downloadable. Needs a plan written
   first; fold in the Session-page detect-polling gap noted above.
