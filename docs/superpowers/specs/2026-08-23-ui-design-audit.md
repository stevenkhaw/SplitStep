# Repo audit and UI design direction — 2026-08-23

Status: audit complete, feature work declared done by the owner. This document
records what was verified, the one defect found, the housekeeping backlog, and
the prioritised frontend direction that follows.

Written at the point where the backend stopped growing. Every plan in
`docs/superpowers/plans/` has shipped; the owner has stated there are no more
features wanted. What remains is correctness cleanup and visual design, and
this is the record of which is which.

## 1. Verification performed

Run against `master` at `ace6238`, on the real library at
`/Volumes/SanDisk_2TB/SplitStep`, not a fixture.

| Check | Command | Result |
| --- | --- | --- |
| Python suite | `pytest -q` | 680 passed, 113s |
| Python lint | `ruff check splitstep tests` | clean |
| Web suite | `npx vitest run` | 458 passed, 40 files |
| Web types + a11y | `npm run check` | 238 files, 0 errors, 0 warnings |
| Web build | `npm run build` | clean, 132.91 kB JS / 24.36 kB CSS gzipped 44.07 / 5.31 |
| Library health | `splitstep … doctor` | ok; videotoolbox, h264_videotoolbox, mps; `2026-08-18/01 ready 3840x2160 rotation 180` |
| Live app | browser against `:8420` | queue mode plays, 22/32, ★6 ×0 ●25; both reels listed and rendered |

Branch state is clean: every feature branch (`feat/reels`, `feat/reel-playback`,
`feat/reel-delete-rename`, `fix/reel-media-containment`, `rename/splitstep`, and
both `claude/*` branches) is merged into `master`. `git branch --no-merged
master` is empty. The SplitStep rename resolved fully — CLI entry point, package
import path and `splitstep.egg-info` all point at the renamed tree.

The live check matters more than the suites here. jsdom has no `<video>`
implementation, so the suites cannot prove that a proxy actually plays; driving
the running server did, and it does.

## 2. The one defect

`routes/Session.svelte` and `routes/Setup.svelte` load their data in an
`$effect` keyed on a reactive `id` prop, and neither clears the previous
attempt's error nor guards against an out-of-order response.

`routes/Library.svelte`, `routes/Reels.svelte` and `routes/Reel.svelte` all do
both. Library even carries the comment explaining why:

> Cleared at the start of each attempt rather than left to linger from a
> previous one -- defensive even though nothing here currently retriggers this
> effect (no reactive reads besides the static `api` import), so a future
> retry/refresh affordance doesn't inherit a stale error alongside a successful
> refetch.

The irony is exact. The three routes that carry the guard are the three whose
effects never re-run. The two that lack it are the two that do — both read `id`,
so both re-run on every navigation between sessions or between sources.

Two distinct failures follow.

**Stale error survives a successful load.** Reproduced in the browser:
navigating to a nonexistent session id raised the banner
`Error: GET /api/sessions/1 -> 404 {"detail":"Session not found"}`; navigating
on to the real session updated the header to `2026-08-18` but left that banner
up. Only a full reload cleared it. Cosmetic, but it makes a working page look
broken.

**Out-of-order responses win.** This is the one with teeth. Without the
`cancelled` flag the other three routes set in their effect teardown, session A's
slow fetch resolving *after* the user has navigated to session B assigns A's
detail over B's. The header reads B while the rally set, the counts and every
keystroke target belong to A. Rally ids are globally unique, so a star lands on
a real rally — just not the one on screen, and with no visible signal that
anything is wrong.

Impact today is small because the library holds exactly one session, so there is
nothing to navigate between. It stops being small the moment a second session is
ingested, which is the expected next use of the tool.

The fix is to adopt the shape the other three routes already use, in both
places. It is not a new pattern; it is applying an existing one consistently.

## 3. Housekeeping backlog

Small, independent, none blocking.

- **Stale worktree.** `/Users/stevenkhaw/Documents/GitHub/BootlegVision-reels` is
  marked prunable by `git worktree list` — a leftover from the directory move
  that the rename plan repaired only partially. `git worktree prune` plus
  deleting the six merged branches clears it.
- **`.claude/` is untracked** and holds two live worktrees under
  `.claude/worktrees/`. It shows up in every `git status`. Wants a `.gitignore`
  entry, with a decision about whether `launch.json` is worth committing (it
  encodes the library path, so probably not).
- **`$SCRATCH` directory in the repo root** — an empty directory created by an
  unexpanded shell variable. Delete.
- **`web/public/label.html:58`** still uses the localStorage key
  `"bootleg-labels-v1"`. The rename missed it. This is the standalone
  validation tool from the 2026-08-20 viewpoint work, superseded by label mode
  in the app; changing the key orphans anything still stored under it, so the
  correct move is probably to leave the key and note why, or delete the file.
- **`CLAUDE.md:211` is stale.** It says "three routes: Library, Setup, Session".
  There are five — `reels` and `reel` shipped with the reel builder.

## 4. Frontend assessment

The frontend is *functionally* in good shape and should not be rewritten. What
it lacks is visual design, and the two should not be confused.

What is already right, and must survive any redesign:

- **Accessibility is genuine, not incidental.** `svelte-check` reports zero a11y
  warnings across 238 files. The queue progress bar is a real `role="slider"`
  with `aria-valuemin`/`max`/`now`, keyboard focus, and both click-to-seek and
  drag — implemented with pointer events specifically so that a plain
  `tabindex="0"` satisfies `click-events-have-key-events`. That is careful work.
- **The logic/presentation split holds.** 2346 lines of tested TypeScript under
  `src/lib/`, 3226 lines of thin Svelte over it, and the component tests that do
  exist (`setup-wizard`, `library-setup-card`, `requeue-on-detail-swap`,
  `session-resegment-guard`) mount the real tree against a mocked `api`. A
  visual pass touches markup and classes, not this.
- **The keyboard model is the product.** Queue review is a hundreds-of-keystrokes
  activity and the bindings are complete. Redesign must not introduce anything
  that requires the mouse.

### Findings

Each measured across `web/src/**/*.svelte` (3226 lines), not impressionistic.

**F1 — No design system.** `src/app.css` is one line: `@import "tailwindcss"`.
There is no Tailwind v4 `@theme` block, therefore no tokens. Colour is chosen
per-callsite, and the roles have collided: `blue-400`, `blue-500`, `blue-600`,
`blue-700`; `red-200/300/400/500/800/900/950`; `amber-300/400/500`;
`yellow-200/400/500`. Star renders amber in one component and yellow in
another. This is the foundational finding — every other visual fix is cheaper
once tokens exist, so it goes first.

**F2 — The type scale has collapsed.** Occurrences: `text-xs` 74, `text-sm` 50,
`text-base` 3, `text-lg` 5, `text-xl` 4. Effectively the entire application is
set at two sizes, both small. There is no size difference between the single
most important fact on screen (which rally am I on) and the least (a keyboard
reference line).

**F3 — The queue status row is unreadable at a glance.** It renders as
`★ ● 🏷 rally 22 / 32 · 12:18.5 · 7.0s     ★6 ×0 ●25 · ~130.9s left at 1×`.
`★` carries two different meanings in one line — a per-rally toggle on the left,
a session total on the right. `●` is unlabelled. Inactive toggles are dark grey
on near-black, so "not starred" and "no such control" look identical.

**F4 — The keyboard legend is a wall.** Eleven shortcuts, always visible, at the
same visual weight as the live data directly above them. It is reference
material presented as content. A `?` overlay, or a persistently de-emphasised
strip, returns the space to the video.

**F5 — There is no motion.** One `transition` in the entire codebase
(`routes/Reel.svelte`). Star and reject are the two actions pressed most often
in the product and neither produces any visual confirmation. This is the
cheapest large perceived-quality win available and should come after the
structural work, not before.

**F6 — List pages are unstyled text.** Library renders one row — a date, and
right-aligned grey metadata — with no thumbnail, no hover state, no affordance
suggesting the row is clickable, and roughly 85% of the viewport empty. Reels is
the same shape, with `rendered` set as plain grey text where a status badge
belongs. Sources already produce `thumbs.jpg` during proxy build; nothing in the
UI consumes it.

**F7 — Raw API errors reach the user.** The banner text is the thrown string
verbatim: `Error: GET /api/sessions/1 -> 404 {"detail":"Session not found"}`.
Method, path and JSON body are implementation detail. (Fixing F7 pairs naturally
with the §2 defect, since both live in the same effects.)

**F8 — Dark-only is hardcoded**, on `<html class="dark">` plus
`body class="bg-neutral-950 text-neutral-100"`. Dark-only is very likely correct
for a tool used on a laptop reviewing night footage — the point is that it
should become a deliberate choice expressed through tokens rather than an
artifact of two literals in `index.html`.

### Order of work

1. **F1 tokens** — mechanical, unblocks everything below.
2. **F2 type scale** — mechanical, same pass.
3. **F3 + F4 queue readability** — the screen the tool actually lives on.
4. **F6 list pages** — thumbnails already exist on disk.
5. **F5 motion** — polish, once the structure is settled.
6. **F7 error copy** — small, and adjacent to the §2 fix.

F8 is a decision to make during F1, not a task of its own.

## 5. What this document deliberately does not propose

**Detector tuning.** The 2026-08-20 validation failed and the reasons are
environmental, not parametric — see
`plans/2026-08-20-camera-viewpoint-validation.md` and its 2026-08-21 correction.
Nothing in this audit changes that. A prettier UI does not make the audio
detector stop measuring the neighbouring court.

**The cross-session rally browser.** Still correctly deferred: it is a filter UI
over one session's rallies until a second session exists.

**Any rewrite.** The findings above are markup, class and token changes. The
router, the `lib/` modules, the component tests and the API surface are all
sound and are not in scope.
