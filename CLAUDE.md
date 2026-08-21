# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Local tennis rally cutter. Phone footage goes in, rally intervals come out, a
keyboard-driven Svelte UI reviews them. One process (`bootleg serve`) is the
API, the media server, the job worker, the inbox watcher, and the SPA host.

Python 3.12 package `bootleg/` + Svelte 5 app `web/`. No cloud, no services,
no CI — a single sqlite database and a folder tree on an external drive.

## Commands

Python lives in the `bootleg` conda env; it is not the shell's default env, so
invoke its interpreter by path (or `conda activate bootleg` first):

```bash
~/miniconda3/envs/bootleg/bin/pytest -q                              # 393 tests
~/miniconda3/envs/bootleg/bin/pytest tests/test_segment.py -q        # one file
~/miniconda3/envs/bootleg/bin/pytest tests/test_segment.py::test_x   # one test
~/miniconda3/envs/bootleg/bin/ruff check bootleg tests
```

Frontend (`cd web`):

```bash
npm run build      # -> web/dist, what `bootleg serve` mounts at /
npm run check      # svelte-check: types + a11y
npx vitest run                       # all test files in web/tests/
npx vitest run tests/queue.test.ts   # one file
```

Running it (library path is required on every command; there is no default):

```bash
bootleg --library /Volumes/SanDisk_2TB/BootlegVision doctor
bootleg --library /Volumes/SanDisk_2TB/BootlegVision serve   # :8420
```

UI iteration wants two terminals — `bootleg serve` for API/media, `npm run dev`
for Vite on :5173 (it proxies `/api` and `/media` to :8420). Anything else:
`npm run build` once, then `serve` alone.

`pytest` runs with `filterwarnings = ["error"]`; a new warning fails the suite.
ruff line-length is 100. ffmpeg must be on PATH. YOLO is never run in tests —
detector output is fixtured or mocked everywhere.

## Architecture

### The library is the app's state

A library is a folder (normally an external SSD) holding `library.db`,
`_inbox/`, `sessions/<date>/sources/NN/{original.*,proxy.mp4,thumbs.jpg,features.jsonl}`,
and `reels/`. `Library.open()` refuses to run unless `library.db` already
exists; only `bootleg init` (`Library.create`) may create one, and it refuses
if one is there. This is load-bearing: an unclean eject leaves an empty
mountpoint that passes `is_dir`/`os.access`, and without the guard sqlite would
silently create a second library on the internal SSD.

### Pipeline (the README is stale here — trust this)

```
watcher -> ingest job -> [human: setup wizard] -> build_proxy job -> detect job
           probe+move    rotation + court quad    transcode+thumbs   features+segment
           needs_setup                            ingested           ready
```

`ingest` is register-only and finishes in seconds — no transcode, no detect.
Both wait on a human confirming orientation and play region, because a
ten-minute encode that was sideways the whole time is worse than a prompt.
Source statuses: `ingesting` → `needs_setup` → `building` → `ingested` →
`detecting` → `ready`, plus `failed`. A session is `needs_setup` if any source
is, `ready` when all are; `reviewed` is a session-only status.

Both the API route and the CLI call one function, `bootleg/setup.py::queue_setup`,
so HTTP and terminal cannot drift on validation.

### Detection is deliberately two-stage

Expensive stage (YOLO11 persons at 5 fps + audio impact detection) writes
`features.jsonl`. Cheap pure function `segment()` (~200 ms) turns frames into
intervals. The split exists so a threshold sweep costs milliseconds and no GPU
— `bootleg segment <source_id> --threshold X --dry-run`, or the UI's re-segment
slider. `detect --reuse-features` skips straight to re-segmenting cached
features, so it will NOT pick up a newly assigned play region.

Scoring has **two profiles**, picked per source by `detect/viewpoint.py::analyze_view`
from the median vertical gap between the two largest person boxes. Always build
params with `segment.params_for_frames(frames)` — constructing `SegmentParams()`
directly silently pins pair mode, which is wrong for half of all sources.

`pair` (camera high enough to see both players at different depths):

```
score(t) = w_both·both_present + w_speed·min(near_v,far_v) + w_lateral·lateral_share
         + w_hits·hit_rate + w_reg·hit_regularity - w_outside·either_outside_region
```

`subject` (camera low enough that the far court collapses onto the horizon —
presence becomes a gate, not a term, and `w_outside` never fires):

```
score(t) = 0 if no box >= subject_min_h, else
           (w_speed·near_v + w_lateral·lateral_share + w_hits·hit_rate
            + w_reg·hit_regularity) / (w_speed + w_lateral + w_hits + w_reg)
```

The two thresholds are on different scales (0.45 pair, 0.25 subject) because
subject's denominator drops `w_both`. Never compare or copy one to the other.

Recall-biased by design: rejecting a false rally is one keystroke, a missed
rally means rescrubbing an hour. Prefer slight over-segmentation.

**Neither profile is validated.** `pair` has never seen real two-player footage.
`subject` was validated on 2026-08-20 and **failed**: camera teardown scores as
a rally, and on ground-level footage the audio detector — subject mode's
dominant input — measures the venue rather than the player. On windows verified
swing-free by a pose track it fires at 0.56 impacts/sec, against 0.67/sec while
actually playing; two of those idle windows individually beat a confirmed
rally's rate. Amplitude, stereo direction and spectral timbre were all tested
and all fail to separate our court from the neighbouring ones.

Read `docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md` before
tuning anything here — including its 2026-08-21 correction, where one clip's
hand label turned out to be wrong. Do not re-fit against audio-impact clusters:
that ground truth is the venue's activity, not the player's.

### Play region

Without a court quad, detection runs whole-frame, `w_outside` never fires, and
players on the adjacent court score as your opponent. One manual step per
source (wizard or `bootleg preset add` + `bootleg setup`). Re-run detect after
assigning one — existing rallies do not change retroactively.

The quad still matters in `subject` mode, just differently. `w_outside` never
fires there — the box-height gate replaces that scoring term — but the quad is
applied earlier and more fundamentally: `split_near_far` filters boxes by
`_in_region` *before* electing near and far, so the quad decides which boxes
exist at all, which one becomes `near`, and therefore where `subject_min_h`
(half the median `near.h`) sits. A wrong quad leaves `near is None` on every
frame and subject mode scores 0 everywhere.

What the quad cannot do is save a ground-level camera. At that height the
opponent and the strangers behind the fence sit on the same horizon line a few
pixels apart, so no quad separates them. And because `analyze_view` measures
the quad's output, a quad admitting adjacent courts inflates the foot
separation and can push a ground-level source into `pair` mode by mistake.

Changing a quad requires a full re-detect, not `--reuse-features`: the filter
is applied when features are built, so cached features are already quad-shaped.

### Jobs

`jobs` table + single-threaded `Worker` claiming one job at a time
(`BEGIN IMMEDIATE` in `jobq.claim`). All handlers are idempotent, so retry is
safe. A `_Heartbeat` thread ticks `heartbeat_at` for the duration of a handler
call so `reclaim_stale()` can tell an abandoned job from a live fifteen-minute
detect. Handler registry: `HANDLERS` at the bottom of `jobs/handlers.py`.

### API

Every route is `def`, not `async def`, so Starlette runs it on a worker thread.
`ThreadLocalConnections` gives each thread its own sqlite connection —
a shared one let one thread's `commit()` finalize another thread's open
transaction. `/media/*` serves proxies with real 206 range support
(`api/media.py`) since `<video>` cannot seek without it, plus on-demand
frame extraction with an evicting cache. The SPA mounts last so `/api` and
`/media` keep priority; a missing `web/dist` logs a warning and serves the API
alone.

### Frontend

Hash router (`lib/router.svelte.ts`) → three routes: Library, Setup, Session.
Session hosts QueueMode (autoplay + star/reject/undo) and TimelineMode
(boundary editing, score curve, re-segment panel).

All logic lives in pure TypeScript under `web/src/lib/` — queue state machine,
undo stack, timeline math, quad geometry, persistence, polling, debounce — and
that is what `web/tests/` covers. Components are thin shells over those modules
and are verified by hand, because jsdom has no `<video>` implementation. Put
new logic in `lib/`, not in a `.svelte` file, or it becomes untestable.

## Conventions that matter

- **Tuning constants are validated against `tests/fixtures/ground_level_source01.jsonl`**,
  a real slice of footage committed as source. The synthetic fixtures hand every
  player `v=2.0`, roughly 8x reality; calibrating against them is what produced
  the bug where every clip came out one hit long. Never tune against synthetics.
- **Comments explain why, not what.** This codebase carries long rationale
  comments on the non-obvious calls (why a guard exists, what broke without
  it). Match that density; do not strip them.
- **Rotation never comes from the file's display matrix.** `make_proxy` passes
  `-noautorotate` and applies the stored `rotation_deg`. Autorotate once scaled
  a 4K clip to 608x1080 and destroyed every detection. `rotation_filter()` is
  the single validator for the angle.
- **Migrations** are numbered `.sql` files in `bootleg/db/migrations/`, applied
  by `PRAGMA user_version`. Add a file; never edit an applied one.
- **`replace_rallies`** runs as one transaction and carries starred/rejected
  across by >50% overlap. Manual boundary edits are intentionally lost — the
  caller confirms first. `det_start_ms`/`det_end_ms` are immutable and record
  what the detector originally guessed.
- **`rally_labels` is the human-judgement corpus, and it survives a re-segment.**
  Rows anchor to `(source_id, det_start_ms, det_end_ms)` — the detector's own
  span — not to a rally row, so a threshold sweep leaves them intact.
  `rally_id` is provenance only and deliberately carries **no foreign key**:
  `replace_rallies` deletes every rally for a source, and a cascade would wipe
  the corpus. `tests/test_labels.py::test_the_corpus_survives_replace_rallies`
  is what catches anyone adding one back. The table is append-only —
  re-labelling appends and `latest_labels` resolves the current row, so a
  corrected judgement never erases the one it corrected.
  Two writers: label mode in the UI (verdict + boundary flags) and
  `POST /api/rallies/{id}/bounds`, which turns every manual drag into a signed
  millisecond correction for free.
- **`bootleg labels score <source_id> --threshold X` is the tuning loop.**
  It re-runs `segment()` over cached features (~200 ms, no GPU) and scores it
  against the corpus, matching candidates to labelled spans by the same >50%
  overlap rule `replace_rallies` uses. Its recall figure is `span recall
  (labelled spans only)` and cannot see play the detector never proposed —
  every label sits on a span it did. Do not rename it to plain "recall";
  letting a metric imply coverage it lacks is what cost the last round.
  `bootleg labels export <source_id>` writes the corpus as JSON for
  `tests/fixtures/`.
- **`features.jsonl` floats are quantized to 4dp** so read/write cycles are
  byte-stable. Round-tripping is exact only for already-quantized values.
- Design rationale and decision log: `docs/superpowers/specs/`, implementation
  plans: `docs/superpowers/plans/`. Read the relevant spec before changing
  pipeline shape or status vocabulary.
- Video files and `yolo11n.pt` are gitignored; `tests/fixtures/**/*.jsonl` is
  explicitly re-included — golden feature fixtures are source.

## Deferred (not missing by accident)

4K clip export, reel building via `-c copy` concat, the cross-session rally
browser, and Reclaim Space are Plan 3. The `reels`/`reel_items` tables and
`clips_dir` exist unused.
