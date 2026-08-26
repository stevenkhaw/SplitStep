# Mac app distribution — design

Date: 2026-08-26
Status: Approved design, awaiting implementation plan

## Goal

A non-technical friend on an Apple Silicon Mac goes from "I recorded tennis on
my iPhone" to "I have my rally clips" without the developer in the room:
download one `.dmg`, open the app, pick where the library lives (a folder on
the Mac or an external drive), drop footage on it, outline the court once,
wait, review with the keyboard UI, export clips and reels. No terminal, no
Python, no ffmpeg install.

This is friend-scale sharing, not a commercial launch. The developer's own
workflow — `splitstep serve` + Vite, conda env, CLI — must survive unchanged.

The product story is fixed: **rough auto-cut rallies, recall-biased, plus fast
keyboard review and clip/reel export.** Detection is a pre-cut, not a referee.
Ground-level footage is the common case (courts rarely allow fence mounts);
fence-mounted `pair` mode is the good tier when available, not a requirement.

## Decisions already made (with the user, 2026-08-26)

- **Mac only, Apple Silicon only.** No Windows work in v1 (torch no longer
  ships Intel-mac wheels, so Intel Macs are out regardless). The pip/browser
  tier improves as a side effect of Phase 1 but is not documented for friends.
- **Tauri shell from the start** (option B over a thin launcher). Polished
  route chosen deliberately; the frozen server is the same artifact either way.
- **Gate 0 before any Tauri code:** validate `pair` mode on the fence-mounted
  clip already dropped in `_inbox`.
- **Friend mode hides tuning tools** (label mode, re-segment panel) behind an
  Advanced setting. Protects the `rally_labels` corpus from stray keypresses.
- **Keep everything on disk.** No delete-originals feature; the Reclaim Space
  rejection stands. The UI surfaces library size; disk management stays the
  user's problem.
- **Unsigned v1.** Right-click → Open is the documented first-launch step.
  Notarization, auto-update: later, if the app spreads beyond friends.
- **Out of scope, named so they stay out:** ball tracking / line calls
  (SwingVision-style in/out is a separate multi-month project; the court quad
  and retained 4K originals keep the door open), Windows, hosted anything,
  doubles, cross-session rally browser.

## Sequencing

- **Gate 0** — run the fence-mount file through the full pipeline, review what
  `pair` mode produces, record the verdict in `docs/superpowers/plans/`. First
  real two-player footage the profile has ever seen. Does not block Phase 1.
- **Phase 1** — server friend-readiness (Python only; benefits the dev
  checkout and pip install immediately).
- **Phase 2** — friend-mode UI, first-run flow, failed-job UX (Svelte).
- **Phase 3** — Tauri shell, PyInstaller bundle, `.dmg`.
- **Phase 4** — numbered reels (independent of 2 and 3; any time after 1).

Each phase is independently shippable.

## Architecture

Tauri v2 shell — mostly configuration plus a few small Rust commands. The
window loads `http://127.0.0.1:<port>`: the UI stays served by the Python
server, **not** Tauri's asset protocol, so the browser tier and the app render
the same origin and `/media` range requests (which `<video>` seeking depends
on) keep working untouched.

Sidecar: PyInstaller onedir freeze of `splitstep serve`, with `web/dist`,
static ffmpeg + ffprobe, `yolo11n.pt`, and one TTF (for drawtext; static
ffmpeg builds often lack fontconfig) inside the bundle.

Shell responsibilities:

- Spawn the sidecar on a free port, health-check `/api`, point the window at it.
- Single instance (Tauri plugin). Two workers on one `library.db` is the
  scenario the `handlers.py` enqueue-race TODO names; the plugin is the first
  line, the race fix (Phase 1) the second.
- Graceful SIGTERM on quit. Jobs are already idempotent and `reclaim_stale`
  covers a hard kill, so interruption is safe by construction.
- Power assertion while jobs are pending — App Nap must not throttle a
  fifteen-minute detect running under a hidden window.
- Relaunch dialog if the sidecar process dies.
- Native folder picker for first run (a browser page cannot read a picked
  folder's absolute path; the dialog must be native). The shell passes
  `--create` only on that first-run launch, always paired with the
  freshly-picked `--library <path>`; every later launch passes plain
  `--library <path>` with no `--create`, so the server-side guard requiring
  the flag alongside `--create` is never in tension with normal startup.
- Dock-icon drop → copy into `_inbox/`; the existing watcher takes over.
- Native notification when detection finishes, by polling `/api/jobs` — no
  server change needed.

## Phase 1 — server changes

1. **Frozen-aware resource resolution.** `web/dist` (today
   `Path(__file__).parent.parent / "web" / "dist"` in `cli.py`, which serves a
   blank page under any non-editable install), ffmpeg/ffprobe (today bare
   `shutil.which`), and the YOLO weights (today an ultralytics auto-download
   into *cwd*, mid-job, needing network) all resolve bundle-first
   (`sys._MEIPASS` / `importlib.resources`), PATH/download as fallback. A
   missing SPA becomes a loud failure, not a log line.
2. **Library resolution order:** `--library` flag → `SPLITSTEP_LIBRARY` env →
   config file (via `platformdirs`). The flag stops being required. A new
   explicit `--create` flag performs first-run init so the Tauri picker can
   initialize a fresh folder; `Library.open`'s refusal to touch a root without
   `library.db` is load-bearing (unclean ejects) and does not change — nothing
   ever auto-creates silently.
3. **New routes:**
   - `POST /api/import` — streamed upload into `_inbox/`. Also powers a UI
     drop zone that works identically in a plain browser (loopback upload).
   - `POST /api/sources/{id}/detect` — kills the QuadEditor dead-end whose
     success message currently instructs the user to open a terminal.
   - `POST /api/jobs/{id}/retry` — failed jobs are currently a terminal state
     with no UI recovery.
   - Failed jobs carry a short human sentence; the traceback demotes to an
     expandable detail (today: six raw frames rendered into a 72px-wide badge
     panel).
4. **Fix the enqueue race** at `jobs/handlers.py` (check-then-act between
   `has_pending_job` and `enqueue`) — `BEGIN IMMEDIATE` or
   `INSERT … WHERE NOT EXISTS`. Self-assessed "not live today" only because a
   single process was guaranteed; the app removes that guarantee.
5. **Inbox honesty.** Non-video suffixes are currently ignored forever with no
   log line. They get logged and surfaced in the UI ("skipped `match.webm` —
   unsupported format"), alongside the existing `_inbox/failed/` quarantine.
6. **Per-library colour profile.** The HLG pin
   (`tv / bt2020nc / arib-std-b67 / bt2020`) moves from a module constant to a
   value locked into `library.db` at the first successful clip export; later
   exports must match *that*. Rationale: the constant is pinned to one
   specific phone; a friend with HDR off (or an older iPhone) would review an
   entire session and then watch every export fail with iPhone-only
   remediation text. One-owner libraries make first-export locking safe, and
   `concat`'s pre-flight already compares every input against a first-clip
   reference, so the mechanics exist. The no-tonemap stance is unchanged —
   mixed-profile *libraries* still refuse; the profile is just per-library
   instead of per-developer-phone. This is the one Phase 1 item with real
   design risk; the implementation plan gives it its own careful task.
   Independently and immediately: fix `README.md`'s capture instruction, which
   says HDR **off** — the setting that produces footage the exporter rejects.
7. **Delete `web/public/label.html`** — ships live at `/label.html`, hardcoded
   to one specific 2026-08-18 proxy.

## Phase 2 — friend-mode UI and first-run

- **Mode flag** `mode: friend | dev` in the config file, exposed to the UI via
  a config endpoint. The app writes `friend` on first run; a dev checkout
  defaults to `dev`. Friend mode hides label mode and the re-segment panel;
  an Advanced toggle in settings re-enables them — hidden, not deleted.
- `shortcuts.ts` stays the single source of keybindings; entries gain a mode
  tag so the inline strip and the `?` overlay filter themselves and stay
  truthful per mode.
- **First-run flow** in the window: welcome → pick folder (native dialog) →
  capture tips (fence mount when the court allows it, 4K30, **HDR on**) →
  drop zone → expectation-setting copy: "we'll ask you to outline your court
  (~20 seconds), then processing takes ~20 minutes."
- **Failed-state truth:** retry + dismiss on failed jobs; a `failed` session
  stops rendering "wait for detection to finish" (nothing is coming); a
  `failed` source stops presenting a silently disabled re-segment slider.
- **Setup wizard copy:** add the *why* (the adjacent-court sentence already in
  QuadEditor) and the duration warning at the moment Start Detection is
  clicked.
- Library size surfaced somewhere unobtrusive (keep-everything policy makes
  this the only disk affordance).

## Phase 3 — bundle and distribution

- PyInstaller onedir from a **locked requirements file** — a fresh dependency
  resolve risks versions the detector was never validated against (already
  named in the 2026-08-23 rename plan), so the freeze env is pinned, not
  re-resolved.
- Tauri bundles the onedir output as a sidecar resource; `.dmg` from Tauri's
  bundler. Expected size 1.5–2 GB (torch dominates).
- Static ffmpeg/ffprobe checked for the flags in use (`-display_rotation`
  needs 7.0+); the bundled TTF rides along for Phase 4's drawtext.
- Ultralytics is AGPL-3.0: a LICENSE/credits note in the app; the public repo
  satisfies source obligations if distribution ever widens.
- v1 update story: re-download the `.dmg`. Migrations are forward-only
  `PRAGMA user_version`, so a new app on an old library just works.

## Phase 4 — numbered reels

Burned-in "3/20" counter with an optional one-line note beneath it, plus the
same counter in the reel preview.

- **Preview counter:** the preview already walks `resolve_items` in order; a
  "3/20" badge (and the note, when present) in the player UI is small.
- **`note` on `reel_items`** (next free migration number — 010 and 011 were
  taken by Phase 1): per-reel-item text, soft cap ~40 characters, entered in
  the builder. It lives on the reel item, not the clip, because clips are
  keyed `(source_id, start_ms, end_ms)` and shared — the same clip can be #3
  in one reel and #11 in another, with different notes.
- **Numbered render** is an opt-in toggle on the reel. Mechanism: each item
  gets one `drawtext` re-encode into a reel-specific temp file **at the locked
  colour profile**, then the existing concat pipeline — `-c copy`, parameter
  pre-flight, duration probe — runs over those intermediates unchanged. Since
  every intermediate is encoded to the same profile, stream-copy concat of
  them stays valid and both guards keep doing their jobs.
- Shared clip files are never modified. Temp intermediates are deleted after a
  successful render.
- Cost honesty in the UI: a numbered render is a real 4K re-encode of every
  clip, ~4–8× footage duration on a laptop. The plain render stays the fast
  default; the toggle's copy states the wait.
- `drawtext` uses the bundled TTF via `fontfile=` — no fontconfig assumption.

## Testing

- Existing suites untouched (718 pytest, 48 vitest files at last count).
- New pytest: import route (streaming, partial-file, non-video), detect and
  retry routes, config resolution order, `--create` semantics vs the
  `Library.open` guard, the enqueue-race fix, per-library colour lock
  (first-export lock, mismatch refusal, message content), numbered-render
  intermediates (profile equality, temp cleanup, note escaping in drawtext).
- New vitest: mode filtering in `shortcuts.ts`, first-run flow state machine,
  failed-state copy selection — all as pure `lib/` modules per the house rule
  (components stay thin shells; jsdom has no `<video>`).
- The `.app` itself: a manual smoke checklist run in a fresh macOS user
  account (poor man's clean machine) — download → right-click open → pick
  folder → drop file → wizard → review → export clip → build reel.

## Risks

- **Gate 0 can fail.** `pair` mode has never seen real footage; if the fence
  clip validates poorly, the capture-tips copy changes and expectations drop,
  but the product story (rough cuts + fast review) already absorbs this.
- **torch/ultralytics under PyInstaller** is a known-wrinkly path (hidden
  imports, data files). Budget a day of hook-fighting in the plan.
- **Per-library colour profile** touches the most carefully guarded code in
  the media layer. Its plan task carries the full rationale from the
  2026-08-21 clip-colour spec and must not weaken the mixed-input refusal.
- **Tauri is new to this repo.** The shell is deliberately thin so the blast
  radius of getting it wrong is the launcher, not the product.
