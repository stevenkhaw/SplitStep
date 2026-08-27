# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Local tennis rally cutter. Phone footage goes in, rally intervals come out, a
keyboard-driven Svelte UI reviews them. One process (`splitstep serve`) is the
API, the media server, the job worker, the inbox watcher, and the SPA host.

Python 3.12 package `splitstep/` + Svelte 5 app `web/`. No cloud, no services,
no CI — a single sqlite database and a folder tree on an external drive.

## Commands

Python lives in the `splitstep` conda env; it is not the shell's default env, so
invoke its interpreter by path (or `conda activate splitstep` first):

```bash
~/miniconda3/envs/splitstep/bin/pytest -q                              # 767 tests
~/miniconda3/envs/splitstep/bin/pytest tests/test_segment.py -q        # one file
~/miniconda3/envs/splitstep/bin/pytest tests/test_segment.py::test_x   # one test
~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
```

Frontend (`cd web`):

```bash
npm run build      # -> web/dist, what `splitstep serve` mounts at /
npm run check      # svelte-check: types + a11y
npx vitest run                       # all test files in web/tests/
npx vitest run tests/queue.test.ts   # one file
```

Running it (`--library` is optional now — resolution order is the flag, then
`SPLITSTEP_LIBRARY`, then a path saved with `splitstep config set-library`; pass
it explicitly whenever a command targets a library other than the configured
default):

```bash
splitstep --library /Volumes/SanDisk_2TB/SplitStep doctor
splitstep --library /Volumes/SanDisk_2TB/SplitStep serve   # :8420
```

`serve --create` initializes a fresh library and requires that `--library`
flag explicitly — it refuses to create one at a path resolved only from
`SPLITSTEP_LIBRARY` or the config file.

UI iteration wants two terminals — `splitstep serve` for API/media, `npm run dev`
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
exists; only `splitstep init` (`Library.create`) may create one, and it refuses
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

Both the API route and the CLI call one function, `splitstep/setup.py::queue_setup`,
so HTTP and terminal cannot drift on validation.

### Detection is deliberately two-stage

Expensive stage (YOLO11 persons at 5 fps + audio impact detection) writes
`features.jsonl`. Cheap pure function `segment()` (~200 ms) turns frames into
intervals. The split exists so a threshold sweep costs milliseconds and no GPU
— `splitstep segment <source_id> --threshold X --dry-run`, or the UI's re-segment
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
source (wizard or `splitstep preset add` + `splitstep setup`). Re-run detect after
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

### Reels

A reel is an ordered list of **clips**, and `reel_items` keys on
`(source_id, start_ms, end_ms)` — the same triple `clip_relpath()` names the
file for. Never on `rally_id`: `001_init.sql` declared it that way with
`ON DELETE CASCADE`, and `replace_rallies` deletes every rally for a source
on each sweep, so the first re-segment would have silently emptied every
reel. Migration `007` replaced the table before it ever held a row. The
cascade on `source_id` is deliberate and is the opposite case — no footage,
no clip.

An item whose span no rally holds any more is an **orphan**. It is badged in
the builder and stays playable, cuttable and renderable; `handle_clip`
therefore treats `rally_id` as optional. A reel is session-agnostic, so
`resolve_items` joins `session_id` and `source_idx` in — the preview's proxy
URL and the clip path both need them.

The `reel` job's `-c copy` is guarded on both sides, because ffmpeg checks
neither and measured behaviour (ffmpeg 9.0.1) is worse than "it would just
error": the concat demuxer exits 0 with empty stderr on mismatched inputs and
reads every clip through the *first* clip's parameters, so a divergent sample
aspect ratio or a clip missing its audio stream produces a full-length,
correct-duration, wrong reel. Duration alone can't see that, so there are two
checks, not one: a pre-flight comparison of every input's codec parameters
against the first clip's (`ClipParams`/`divergences` in `media/concat.py`),
and a post-hoc probe of the output's duration against the sum of the inputs.
Either failing falls back to a full re-encode at the locked profile, logged.
Render **refuses** while any clip is missing, naming the count, and never
auto-enqueues the cuts: the builder's *Cut missing clips* is the only button
that starts an encode.

Preview seeks the **proxy** to each item's span in order, reusing `VideoDeck`
— it already plays a source between in/out points and preloads the next span
across sources. It shows 1080p and cannot reveal a `-c copy` artifact (that
is the duration/parameter checks' job); what it shows exactly is timing.

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

Hash router (`lib/router.svelte.ts`) → five routes: Library, Setup, Session,
Reels, Reel.
Session hosts QueueMode (autoplay + star/reject/undo) and TimelineMode
(boundary editing, score curve, re-segment panel).

All logic lives in pure TypeScript under `web/src/lib/` — queue state machine,
undo stack, timeline math, quad geometry, persistence, polling, debounce,
plus `shortcuts.ts` (the one place a keybinding is written down; the inline
strip and the `?` overlay both render from it), `status.ts` (status → label +
tone for the list cards), `jobs.ts` (job phase names and batch elapsed),
`split.ts` (cutting a rally in two and putting it back, with the same idx
ordering `_renumber` uses), `flash.ts` (the verdict confirmation) and
`errors.ts` (`ApiError` → a sentence) — and that is what `web/tests/` covers. Components are thin shells over those modules
and are verified by hand, because jsdom has no `<video>` implementation. Put
new logic in `lib/`, not in a `.svelte` file, or it becomes untestable.

### Design tokens

`web/src/app.css` holds the `@theme` block, and it is the only place a colour
or a type size is chosen. A component reaching for a raw Tailwind palette step
(`bg-neutral-800`, `text-blue-300`) or an arbitrary size (`text-[11px]`) has
escaped the system — bring the value back to `app.css` instead.

Colour: `bg` / `surface` / `surface-2` / `line`, text `fg` / `dim` / `faint`,
and four semantic tokens — `star`, `point`, `accent`, `danger`. The base ramp
is violet-shifted rather than neutral grey so the chrome sits with night-court
footage. All three text tokens clear 4.5:1 on both `bg` and `surface`, which is
what lets `faint` carry real functional text like the keyboard legend. Filled
`accent` and `danger` buttons need an explicit `text-bg`: both tokens are light
enough that a white label measures about 2:1.

**Reject has no colour, deliberately.** Detection is recall-biased, so
rejecting is the most frequent action in the app; red would state "error" about
the routine case. Reject is `text-faint` plus a strikethrough — it recedes.
That keeps `danger` meaning an actual failure (a failed job, a missing clip, a
render refusing), which is how the re-segment warning and the `missing` clip
badge are now coloured. `star` and `point` are different axes, not two grades
of one, so they are warm and cool rather than one hue twice.

Type: five roles — `display` / `title` / `body` / `data` / `caption` — not a
size ramp. Naming sizes by magnitude is what let the whole app collapse into
`text-xs` and `text-sm`. `font-data` is the mono role and carries
`tabular-nums`; every timecode, duration, count, threshold and confidence
belongs in it, or the status line jitters sideways as the playhead ticks.

Video letterboxes stay literal `bg-black` — `bg` is `#0b0b0e` and shows as a
seam around the frame.

Tests that assert on a class name are asserting on a token, not a palette step;
four had to be retargeted during the migration and would again.

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
- **The clip profile pins colour, and `make_clip` refuses a source that
  disagrees.** `tv / bt2020nc / arib-std-b67 / bt2020` — HLG, what an iPhone
  records and what every existing clip carries. Strict equality, untagged
  included, and no override: this ffmpeg has neither libzimg nor libplacebo,
  so there is no correct tonemap in either direction and a relabel would make
  a file look right while being wrong. `probe.color_tag` is the one place
  ffprobe's two spellings of "missing" collapse to `None`, and `concat`'s
  pre-flight shares it so the two layers cannot disagree. Synthetic test
  sources must be tagged via the `hlg_setparams` fixture — with a lavfi input
  the `-color_*` output flags silently drop primaries and transfer.
- **Migrations** are numbered `.sql` files in `splitstep/db/migrations/`, applied
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
- **A rally with `det_start_ms IS NULL` was made by a human, not proposed by
  the detector.** Timeline mode's `C` cuts one rally in two; the second half
  carries no detector span, because `rally_labels` anchors on
  `(source_id, det_start_ms, det_end_ms)` and two halves inheriting one span
  would collide in the corpus — the second labelled would silently overwrite
  the first. Giving each half its own span is worse: `det_*` records what the
  detector *originally guessed*, so spans it never produced are fabricated
  training data. The absence is the marker rather than a boolean beside it,
  since a boolean can drift out of agreement with the columns it describes.
  Consequences, all of them load-bearing: `merge_into_previous` (`U`) refuses
  any rally that has a det span, so an undo can never delete a row the corpus
  is anchored to; `/bounds` skips its corpus write while `/label` and
  `/label/retract` both refuse outright — there is no detector span for
  either a judgement or its retraction to attach to; `LabelController` filters
  these rallies out in its constructor so `index`/`total` stay truthful; and
  `editedBoundaryCount` excludes them, counted instead by a separate
  `splitCount` — the two feed one shared `resegmentLossPhrase`, so the
  click-time confirm dialog and the panel's always-visible warning can never
  disagree about what a re-segment costs. A re-segment destroys them, like
  every other manual edit — `replace_rallies` rebuilds from detector
  intervals and a hand-made rally has none.
- **The current label for a span is resolved, not just read.** Newest row per
  `(source_id, span_start_ms, span_end_ms)` by `labelled_at DESC, rowid DESC`,
  then dropped if it carries neither a verdict nor a corrected span. Only a
  retraction (`retract_label`, what label mode's `U` writes, migration 004)
  reaches that state, and a span in it is exactly as unjudged as one nobody
  opened — so label mode, the exporter and the scorer all agree without any of
  them knowing retractions exist. `latest_label_for_span` deliberately does not
  filter: a writer that could not see a retraction would carry the verdict it
  withdrew forward on the next drag. Undo retracts the reviewer's judgement
  only; a drag's `true_*` measurement is carried across it untouched.
- **Label writes are serialised per rally in the client** (`LabelWriter`,
  `web/src/lib/labels.ts`). Every POST carries the span's whole state and the
  server resolves by latest row, so an out-of-order burst used to leave the
  corpus holding a state the reviewer had already moved on from. One in-flight
  write per rally; an older write's failure with a newer one queued is absorbed
  rather than reverted; a failure with nothing behind it restores the last
  state the server accepted. Two tabs on one rally are still unordered — that
  would need a server-side revision.
- **`splitstep labels score <source_id> --threshold X` is the tuning loop.**
  It re-runs `segment()` over cached features (~200 ms, no GPU) and scores it
  against the corpus, matching candidates to labelled spans by the same >50%
  overlap rule `replace_rallies` uses. Its recall figure is `span recall
  (labelled spans only)` and cannot see play the detector never proposed —
  every label sits on a span it did. Do not rename it to plain "recall";
  letting a metric imply coverage it lacks is what cost the last round.
  `splitstep labels export <source_id>` writes the corpus as JSON for
  `tests/fixtures/`.
- **`features.jsonl` floats are quantized to 4dp** so read/write cycles are
  byte-stable. Round-tripping is exact only for already-quantized values.
- **A route `$effect` keyed on a reactive `id` must clear its state and guard
  its response.** `App.svelte` renders `<Session id=…>` and `<Setup id=…>`
  unkeyed, so navigating between two sessions (or two sources) swaps the prop
  on the live instance rather than remounting. Both effects therefore set
  `error = null` and drop the loaded object up front, and both return a
  teardown flipping a `cancelled` flag their `.then`/`.catch` check. Without
  it a superseded response assigns over the one on screen: rally ids are
  globally unique, so a star lands on a real rally that is not the one the
  header names. Setup clears `points`/`selectedPresetId` too — `start()`
  submits `api.setup(source.id, …)`, so a carried-over quad is one click from
  a rebuild and a fifteen-minute detect on the wrong source.
  `tests/route-effect-staleness.test.ts` covers all four shapes.
- Design rationale and decision log: `docs/superpowers/specs/`, implementation
  plans: `docs/superpowers/plans/`. Read the relevant spec before changing
  pipeline shape or status vocabulary.
- Video files and `yolo11n.pt` are gitignored; `tests/fixtures/**/*.jsonl` is
  explicitly re-included — golden feature fixtures are source.

## Deferred (not missing by accident)

The cross-session rally browser is the only piece of Plan 3 still deferred —
it is a filter UI over one session's rallies until a second session exists.
Reels shipped: `reel_items` is keyed on `(source_id, start_ms, end_ms)` by
migration 007, the `reel` handler concatenates with `-c copy`, and `/reels`
plus `/reels/:slug` build and preview them. 4K clip export shipped —
`splitstep clips export`, the `clip` handler, and `clips_dir` are live.
Numbered renders shipped too: a reel can render with a burned-in "3/20"
counter and a per-item note (migration 012), composited as a PIL-rendered
PNG via ffmpeg's `overlay` — never `drawtext`, which this ffmpeg build
lacks — into per-clip intermediates at the library's locked colour profile,
which the existing guarded concat then joins; the plain render is untouched
and stays the fast default.

**Reclaim Space is rejected, not deferred.** The library sits on a 2TB external
drive that holds ~110 hours of play keeping everything, so deleting originals
frees space nobody needs and forfeits the 4K source for every rally that was
not flagged before the delete — including spans a later re-segment invents.
`has_original=0` still routes `handle_clip` to the proxy, but that is now
recovery from a file lost outside the app, not a feature. Nothing clears it.
