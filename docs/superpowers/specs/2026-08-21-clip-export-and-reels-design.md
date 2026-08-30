# SplitStep — Clip Export and Reels

**Date:** 2026-08-21
**Status:** Approved and implemented — `splitstep clips export`, the `clip`
and `reel` job handlers, and `/reels` + `/reels/:slug`. Migration 007 rekeyed
`reel_items` to `(source_id, start_ms, end_ms)` before it ever held a row.
**Extends:** `docs/superpowers/specs/2026-08-19-splitstep-design.md` §7
**Corrects that document in three places** — see §2.

**Implemented in two plans.** The first covers §3 (the point flag) and §4
(clip export): the half where every irreversible decision lives — the locked
profile, span-derived naming, baked rotation — and the half that is useful on
its own, since clips play in any player. The second covers §5–§6 (reels, the
builder, preview) and is written after the first lands, so it benefits from
what real footage teaches about the first.

---

## 1. Problem

Review produces judgements and nothing else. A session can be fully reviewed,
every good point marked, and there is still no way to get a single second of
video out of the library. `splitstep` has never cut a clip.

The original design covers this as Plan 3 and covers it well: a locked clip
profile, software encoding so `-c copy` concat stays valid forever, `clip` and
`reel` job types, and the `reels`/`reel_items` tables — which exist in
`001_init.sql`, unused, waiting. This spec is that plan, minus the cross-session
rally browser, plus the corrections listed below.

It also fixes a modelling problem the reviewer hit in practice. `starred` was
doing two jobs. Reviewing a filmed tiebreaker, every point got starred — double
faults and missed returns included — because "star" was the only mark available
and "this is a point" was the thing worth recording. That leaves no way to say
"this one was good". The two questions are different and both are worth asking.

## 2. What this corrects in the original design

**Rotation is absent from it entirely.** The word does not appear in
`2026-08-19-splitstep-design.md`. That document predates migration `002`,
which added `sources.rotation_deg`. Meanwhile CLAUDE.md is unambiguous:
rotation never comes from the file's display matrix, `make_proxy` passes
`-noautorotate` and applies the stored angle, and `rotation_filter()` is the
single validator. Clips must do the same. The first real source this ships
against is `rotation 180`, so a clip cut as originally specified comes out
upside down — or, worse, ffmpeg's autorotate fires inconsistently and it
becomes a mystery aspect bug months later.

**Clip filenames keyed on `rallies.idx` are unstable.** The original names
clips `clips/rally-007.mp4`. `_renumber` reassigns `idx` across a whole session
on every `replace_rallies`, so `rally-007` points at a different rally after any
threshold sweep. §4 replaces this with a span-derived name.

**`reel_items.rally_id` carries `ON DELETE CASCADE`.** `replace_rallies` deletes
every rally for a source on every sweep, so the first re-segment after building
a reel would silently empty it. This is the same trap `rally_labels` was
deliberately built to avoid, and this table walked into it. §5 replaces the
table before it ever holds a row.

## 3. The point flag

A third review flag on `rallies`, beside `starred` and `rejected`. Not a label:
`rally_labels` answers "what did the detector get wrong", this answers "what is
this clip for". Different question, different table, different lifetime.

| flag | means | key |
|---|---|---|
| `rejected` | not a rally at all — a bad detection | `X` |
| `point` | a point was played out, whatever the quality | `P` |
| `starred` | a highlight worth showing | `S` |

The three are independent booleans. `starred` is expected to be a subset of
`point` in practice but is not constrained to be — a warm-up rally can be worth
watching without being a point, and enforcing the containment would make the
reviewer argue with the tool.

`P` toggles and does not advance, the same reasoning that removed auto-advance
from star and reject.

It **does** stamp `reviewed_at`, via the same `COALESCE` those two use. All
three flags are rulings on the clip, and `reviewed_at` records that a human has
ruled on a rally at all — so a reviewer who marks every point of a tiebreaker
and stars none must still end with a reviewed session. This is where the
analogy to labels breaks: a label answers "what did the detector get wrong",
lives in its own table, and deliberately stays out of
`refresh_session_review_status`; `point` is a review flag in the same family as
`starred` and `rejected` and belongs inside it.

```sql
-- 005_point_flag.sql
ALTER TABLE rallies ADD COLUMN point INTEGER NOT NULL DEFAULT 0;

-- One-time reinterpretation of existing data, and it is a reinterpretation
-- rather than a copy. Until now a star meant "a point was played out" -- the
-- first real session starred all 24 points of a tiebreaker, double faults
-- included. Moving that meaning to `point` leaves `starred` free to mean
-- "a highlight I like", which is what it was always supposed to mean.
--
-- Stars are cleared rather than left set. If the second pass never happens,
-- an empty highlight set is honest and a set that still silently means
-- "point" is not.
UPDATE rallies SET point = 1 WHERE starred = 1;
UPDATE rallies SET starred = 0;

CREATE INDEX idx_rallies_point ON rallies(point) WHERE point = 1;
```

`PRAGMA user_version` guarantees the backfill runs exactly once.

**`replace_rallies` must carry `point` across by >50% overlap**, exactly as it
already does for `starred` and `rejected`. Missing this silently wipes every
point mark on the first re-segment, which is precisely the failure mode this
codebase keeps re-learning. It gets its own test.

## 4. Clip export

### 4.1 Naming

```
sessions/<date>/clips/<source idx>-<start_ms>-<end_ms>.mp4
e.g.                  01-738500-745500.mp4
```

Span-derived, for the same reason the label corpus anchors to detector spans:
it is stable across re-segmentation, it is self-identifying on disk, and a
sweep that reproduces an identical span finds its clip already cut. `idx` is
not stable and must not appear in a filename.

This makes staleness a pure function. `clip_relpath(source_idx, start_ms,
end_ms)` computed from the rally's *current* bounds either exists or does not.
No new columns, no filename parsing, no mtime comparison. `rallies.clip_path`
is still set on completion, but **nothing reads it** — not the UI, not the API,
not `plan_export`, which asks the filesystem. It was written for Reclaim Space,
which §9 now rejects. It is kept because it is the only record of what was cut
for a span its rally no longer has, which is what §4.4's sweep nulls out when
it deletes that file.

Export is therefore incremental by construction: exporting a set enqueues jobs
only for spans with no clip on disk. Re-exporting after marking three more
points costs three encodes, not twenty-four.

### 4.2 The locked profile

```
mp4 · H.264 High · yuv420p · 3840×2160 · SAR 1:1 · 30 fps CFR · CRF 20
     · AAC 128k 48 kHz stereo, always present
```

**`SAR 1:1` and "always present" were added after the fact**, and they are not
new decisions — they are two properties the profile always relied on and never
stated, so nothing conformed them. See §4.3.

**Changing this breaks `-c copy` against every clip ever cut.** It is locked in
the original design and stays locked here.

**And it breaks quietly.** This document assumed the concat demuxer refuses
streams whose codec parameters differ. Measured on ffmpeg 9.0.1 it does not: it
exits 0 with empty stderr and reads every input through the *first* clip's
parameters, so a mismatched sample aspect is ignored (the clip plays at the
wrong shape) and a clip with no audio stream contributes no audio (the reel's
track stops early while the picture runs on). A loud failure would have been
the better outcome; this is why the parameters are conformed at cut time
instead of trusted to be checked at concat time.

Clips are software-encoded (libx264, `-preset medium`) on purpose. Hardware
encoders emit vendor-specific SPS/PPS headers, so a clip cut on the Mac and one
cut on the 4070Ti would fail to concat cleanly or concat with artifacts.
libx264 produces identical headers on every machine, permanently.

**Measured cost, replacing this section's original estimate.** It runs at
**4–8x realtime** on this Mac — roughly 80–160 s for a 20-second clip — and the
first real 24-point export took about **30 minutes**, against the ~9 minutes
estimated here before anything had been cut. That is a difference in kind
rather than degree: nine minutes is a coffee, half an hour is a decision about
whether to start now. `handle_clip` therefore reports `jobs.progress` — see
§4.5.

Sources that do not match are conformed at cut time rather than at concat time:
sub-4K is upscaled and padded, 60 fps is decimated, 29.964 fps — the first real
source — is conformed to 30 CFR at a cost of ~0.12% duplicated frames,
non-square pixels are scaled out and pinned to 1:1, and a source with no audio
stream gets a synthesized silent one. A mixed-parameter clip library cannot be
concatenated with `-c copy`, and that guarantee is worth more than avoiding an
upscale.

### 4.3 The `clip` job

Registered in `HANDLERS`, idempotent (overwrites its output).

```
-noautorotate  -ss <start_ms> -i <src>
               [-f lavfi -i anullsrc=channel_layout=stereo:sample_rate=48000]
-t <duration_ms>  [-map 0:v:0 -map 1:a:0]
-vf  [scale=<display width>:<coded height>,]      # only when SAR != 1
     <rotation_filter(rotation_deg)>,
     scale=3840:2160:force_original_aspect_ratio=decrease,
     pad=3840:2160:(ow-iw)/2:(oh-ih)/2,
     setsar=1
-r 30
-c:v libx264 -profile:v high -pix_fmt yuv420p -crf 20 -preset medium
-c:a aac -b:a 128k -ar 48000 -ac 2
-movflags +faststart
```

The bracketed parts are conditional on what the source turns out to be, which
is why `make_clip` probes it before building the command.

Six things are load-bearing in that argument order:

- **`-noautorotate` precedes the input**, and the angle is baked by
  `rotation_filter()` — the single validator. A clip that left rotation as a
  container flag would concat into a reel that flips halfway through.
- **Rotation is applied before scale and pad.** At 90 or 270 the dimensions
  swap, so scaling first pads against the wrong axis. Irrelevant at 180, wrong
  the moment the camera is mounted sideways.
- **`-r 30` conforms the frame rate.** Mismatched rates break `-c copy`.
- **`-ss` precedes `-i`.** Modern ffmpeg makes input seeking both fast and
  frame-accurate when the output is re-encoded, which it always is here. Cut
  accuracy is not optional: an in-point that lands on the previous keyframe
  would put a second of the wrong footage at the head of the clip, and the
  reviewer trimmed those boundaries by hand.
- **`-preset medium`, software only.** Determinism over speed; see §4.2.
- **`setsar=1` closes the chain, and a de-anamorphizing scale opens it.**
  Sample aspect ratio is a property of the source that the locked profile
  never pinned, and libx264 writes it into the SPS VUI — so a non-square-pixel
  source produced a clip whose codec parameters differed from every clip
  already cut. `setsar=1` alone would fix the parameter and wreck the picture,
  declaring pixels square without making them square; the leading scale to the
  source's *display* width is what conforms it honestly, and it precedes
  rotation because SAR describes the coded frame and `transpose` inverts it.
  `setsar=1` goes last rather than beside it because scale and pad each
  recompute output SAR to preserve display aspect, and a rounding remainder in
  either could put a fraction back.
- **A silent source gets synthesized silence, not a refusal.** Whether there
  is an audio stream at all is the other unpinned source property: a
  video-only clip either fails to concat against clips that carry audio or
  drops the track for the rest of the reel. `anullsrc` at the profile's own
  rate and layout, bounded by the same `-t` (it is an infinite input), with an
  explicit `-map` once there are two inputs. A source with no microphone is
  still good footage; the unconcatenatable clip is the failure worth avoiding.

Source is `original.*` (via `find_original`) when `sources.has_original`, else
`proxy.mp4`, which the same scale/pad chain upscales to the locked profile.

**Nothing flags such a clip as 1080p-sourced, and this section used to say it
did** — as does §7 of `2026-08-19-splitstep-design.md`, which it inherited
the claim from. No badge was ever built: `has_original` reaches the frontend as
a field on the source type and no component reads it. The claim was repeated in
`handle_clip` and in that handler's test, so a comment this codebase treats as
load-bearing said something untrue in three places at once. All three now say
what is actually the case: the clip is upscaled and indistinguishable from a
4K-sourced one except by eye. If the badge is worth having, its home is the
reel builder's per-item row (§6.4), where there is already a per-clip status
for it to sit beside; there is no clip-listing surface today for it to live on.

Free space is checked before the job starts. A 4K clip that dies at 90% is
worse than a job that refuses to run.

### 4.4 Orphaned clips

Span-derived naming (§4.1) makes export incremental, and it makes the inverse
question answerable too: a clip is **orphaned when no rally's current bounds
resolve to its name**. A threshold sweep that moves a span strands the file cut
at the old one — nothing enumerated it and nothing removed it — and at roughly
2 MB per second of clip a few sweeps is real dead weight on the drive.

`find_orphan_clips(library, conn, session_id)` answers it with no bookkeeping
at all, for the same reason `plan_export` needs none: membership is by name.
Two rules keep it from claiming more than it should.

- **`parse_clip_name` is the gate, not a `*.mp4` glob.** It admits exactly what
  `clip_relpath` writes and nothing else, so a file the user dropped into
  `clips/` is left alone — and so is an encode in flight, whose dot-prefixed
  `.part` name (§6.4) carries segments the pattern does not admit. A glob would
  skip the temp files by accident rather than on purpose, and would sweep up
  the stranger's file.
- **The review flags play no part.** A clip whose rally was rejected or
  un-pointed after it was cut is not orphaned: a rally still holds those
  bounds, and the verdict is one keystroke from changing back.

Two commands, and the destructive one says so:

```
splitstep clips orphans <session_id>          # list: name, source, span, size
splitstep clips prune   <session_id>          # dry run — lists, deletes nothing
splitstep clips prune   <session_id> --yes    # actually deletes
```

`prune` takes the list `orphans` produced rather than re-deriving it, so what
a user was shown is what gets deleted. It clears `rallies.clip_path` **before**
unlinking, and that order is deliberate: the column records what *was* cut
rather than what the current bounds imply, so a rally whose bounds were dragged
still names the file being swept — and `_carried_clip_path` copies that claim
onto the new row at every exact-span re-segment, so a stale one propagates
rather than decays. Clearing first means a failed unlink leaves a column that
understates what is on disk, which is the harmless direction; the other order
leaves a row asserting a clip that is not there.

There is deliberately **no automatic sweep and no HTTP route**. Deleting a clip
costs a four-to-eight-minute re-encode to get it back and there is no undo, so
the destructive reading has to be the one the user typed on purpose.

### 4.5 Progress

`jobs.progress` has existed since `001_init.sql`, is served by `/api/jobs`, and
until now nothing ever wrote it — so the only feedback across a half-hour
export was the badge's *N jobs running*, equally true at the first second and
the last. `handle_clip` now reports it.

The mechanics, and the constraints that shaped them:

- **ffmpeg reports its own position.** `-progress pipe:1` writes machine-
  readable blocks to stdout, which is free because every call writes its real
  output to a file. stderr goes to a temp file rather than a second pipe —
  reading one pipe while the other fills is the classic deadlock, and stderr
  is what lands verbatim in `jobs.error`.
- **Handlers are handed a reporter, not a job id.** A handler that knew its id
  would also have to know the queue's schema; all it has to say is what
  fraction of it is done. The default is a no-op, so the CLI and the tests
  call handlers directly with no row behind them.
- **One callback per whole percent.** Each becomes a committed row on the
  worker's connection — the one the heartbeat thread shares — and ffmpeg emits
  a block twice a second.
- **Progress and `timeout` are mutually exclusive in `run_ffmpeg`.** The
  streaming path blocks on ffmpeg's stdout, so a `timeout` passed alongside
  would be a guarantee it does not make. Nothing needs both: progress is for
  background jobs, timeouts are for request handlers.

Only `clip` reports. `build_proxy` and `detect` run longer still but run
unattended right after ingest, and — more to the point — a job averaged in at
zero would pin the badge at 0% for fifteen minutes, which reads as stuck.
`activeJobsLabel` (`web/src/lib/jobs.ts`) therefore shows no percentage at all
until some active job has one, and averages over every active job, queued ones
included at zero, because the question during an export is how far through the
batch it is rather than how far through the clip currently encoding.

## 5. Reels

`reels` is unchanged from `001_init.sql` — `id, name, slug, rendered_path,
rendered_at, dirty, created_at` — and stays session-agnostic, which is what
lets a reel span sessions once there is more than one.

`reel_items` is replaced. It has never held a row, so there is no data to
migrate.

```sql
-- 006_reel_items_by_span.sql
DROP TABLE reel_items;
CREATE TABLE reel_items (
  reel_id   TEXT NOT NULL REFERENCES reels(id) ON DELETE CASCADE,
  -- Deliberately NOT a rally. replace_rallies deletes every rally for a source
  -- on each threshold sweep, so `rally_id REFERENCES rallies(id) ON DELETE
  -- CASCADE` -- what 001 declared -- would silently empty every reel on the
  -- first re-segment. A reel is an ordered list of CLIPS, and a clip is a span
  -- of a source, which is also exactly how the clip file is named.
  --
  -- Cascading on source_id IS correct: delete the source and the footage is
  -- gone, so the clip is meaningless. Cascading on a rally is not, because a
  -- rally is a guess the detector re-makes every sweep.
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  start_ms  INTEGER NOT NULL,
  end_ms    INTEGER NOT NULL,
  position  INTEGER NOT NULL,
  PRIMARY KEY (reel_id, source_id, start_ms, end_ms)
);
```

The builder resolves rally metadata — duration, thumbnail, confidence — by
looking up a rally at that span. An item whose rally has since vanished renders
as orphaned but still playable and still renderable, never silently dropped:
the clip on disk is what the reel is made of, and it still exists.

### 5.1 The `reel` job

Concat demuxer with `-c copy` into `reels/<slug>.mp4`. Instant, because every
clip already shares the locked profile.

**Then probe the output and compare its duration against the sum of the
inputs.** On mismatch, fall back to a full re-encode and log it. Silent
`-c copy` failure is a known ffmpeg trap; a reel that is quietly missing its
last four points is worse than one that took thirty seconds longer.

**Render refuses while any item has no clip on disk**, naming the count. The UI
offers to cut them. Auto-enqueueing would turn a button labelled "render" into
an unannounced nine-minute encode.

`dirty` is set on any membership or order change and cleared on a successful
render.

## 6. UI

### 6.1 Queue mode

`P` joins `S` and `X`: toggles `point`, does not advance, renders an indicator
beside the star. Help line updated.

### 6.2 End of queue

The "Session reviewed" panel is currently a dead end carrying a comment that
reserves this exact spot. It gains two buttons with live counts:

```
Reel of all points (24)        Reel of starred (0)
```

**These are additional buttons, not replacements.** Plan A already put
*Export point clips (N)* and *Export starred clips (N)* on this panel, which
cut clips and create no reel. The panel therefore ends up with four actions in
two rows — cut, and compile — and the reel buttons must not absorb or replace
the export ones. Creating a reel does not cut anything; §6.4's *Cut missing
clips* stays the only path that starts an encode, so a button labelled
"reel" never silently launches half an hour of work.

Each creates a reel named `<session date> <set>` — `2026-08-18 points`,
`2026-08-18 starred` — with membership in chronological order, and opens the
builder. The slug is that name lowercased with spaces replaced by hyphens,
suffixed `-2`, `-3` … on collision, since `reels.slug` is UNIQUE and a name is
free to repeat.

**A second click merges additively**: spans not already in the reel are
appended, existing entries and their hand-ordering are untouched, and nothing
is removed. Overwriting membership would silently discard a manual reorder —
the same class of mistake `replace_rallies` makes with boundary edits, which
has already cost this project a 9.6-second rally. Removal stays an explicit
action in the builder.

### 6.3 Routes

Two new hash routes join Library / Setup / Session:

- `/reels` — every reel with its item count and rendered/dirty state, plus
  **New reel** (name → slug) for composing one by hand.
- `/reels/:slug` — the builder.

### 6.4 The builder

An ordered list, one row per item: thumbnail, duration, source, and a
clip-status badge (*ready* / *missing*). A clip counts as ready only when a
file exists at exactly `clip_relpath(...)`. Plan A writes each encode to a
dot-prefixed `.part` sibling and `os.replace()`s it onto the final name only
on success, so a partially-written clip never occupies the real name — but
anything enumerating `clips/` must still skip dot-prefixed files rather than
globbing `*.mp4` blindly, or it will count a dead temp file as a clip. Plus:

- **Drag to reorder.** Hand-rolled pointer dragging, not a dependency —
  `ZoomBand` and `QuadEditor` already establish that pattern here. All the math
  lives in `web/src/lib/reorder.ts` as pure functions (`moveItem(list, from,
  to)`, pointer-y → target index) so it is testable; the component owns only
  `getBoundingClientRect`.
- **Remove** an item. Explicit, because the additive merge never removes.
- **Add rallies** — a picker over the session's rallies filtered to points /
  starred / all, checkboxes, appended at the end.
- **Cut missing clips** — reuses Plan A's `plan_export`/`ExportPlan` rather
  than reimplementing the decision. Note it reports four separate outcomes —
  `queued`, `already_cut`, `in_flight`, `unavailable` — and they must stay
  separate here too: collapsing them is what once made a second press
  mid-encode report everything as done. Progress goes through the existing
  jobs badge; do not build a second progress UI.
- **Render** — enqueues the `reel` job, disabled while clips are missing with
  the count named on the button so the reason is visible.

### 6.5 Preview

Preview plays the **proxy**, seeking to each item's span in order.

The original design proposed chaining clip files through the dual-`<video>`
component. Seeking the proxy is strictly simpler and reuses the machinery
exactly as built: `VideoDeck` already takes a source plus in/out points, fires
`onended` at the out-point, and preloads the next span into its second element
— across different sources. Preview is that loop with the star/reject
machinery removed. The ordering and advance logic is a small pure controller in
`web/src/lib/`; the component is a shell.

Its limits, stated rather than discovered later: it shows 1080p, and it cannot
reveal `-c copy` artifacts — that is what §5.1's duration probe is for. What it
does show exactly is **timing**, because reel items carry their own
`start_ms`/`end_ms`, so preview plays precisely the span the clip contains.
Seeks between non-adjacent spans stall slightly, absorbed by the same dual-
element preload the review queue relies on.

## 7. Errors

- Free space checked before `clip` and `reel`, refusing with the shortfall
  named.
- ffmpeg stderr captured verbatim into `jobs.error` on non-zero exit; the job
  is marked `failed`, never silently done. The failure to avoid is a zero-byte
  clip that looks like a decode bug three days later.
- Render refuses on missing clips, naming the count.
- An item whose rally has vanished renders as orphaned, never dropped.
- Concat duration mismatch triggers the re-encode fallback and logs it.

## 8. Testing

- **`clip` output parameters, asserted with `ffprobe`** against a generated
  `lavfi testsrc`: resolution, frame rate, pixel format, profile, audio rate
  and channels. This is the most important test in the plan — encode-profile
  drift is the one silent failure that breaks `-c copy` against every clip ever
  cut, and it would not surface until a reel rendered wrong.
- **The two source-derived parameters**, each against a source that has it: an
  anamorphic source yields SAR 1:1 *and* an unstretched picture (dimensions
  cannot tell those apart — sample the letterbox), and a silent source yields
  an aac/48000/stereo track bounded to the clip's own duration.
- **Orphans**: a moved span strands its clip, an identical span does not, and
  neither an in-flight `.part` file nor a file this library did not name is
  ever claimed. Plus `prune` clearing `clip_path` before it unlinks.
- **Progress**: fractions arrive in order, end at exactly 1.0, and cost at most
  one callback per percent; a failing streaming run still carries ffmpeg's
  stderr; the worker turns a handler's reports into committed `jobs.progress`
  rows visible to another connection *while the job is still running*.
- **Rotation is baked, not flagged**: cut from a `rotation_deg=180` source and
  assert the output carries no rotation side-data.
- **Concat**: three clips, concat, assert duration equals the sum. Plus the
  mismatch path taking the re-encode fallback.
- **`replace_rallies` carries `point` across by overlap** — the test that stops
  a re-segment wiping every mark.
- **Migration `005`** backfills exactly once and correctly; **`006`** recreates
  `reel_items` with no rally foreign key.
- **Span-derived clip paths**: an identical span after a re-segment resolves to
  the same file, so export skips it.
- **Frontend pure logic** in `web/tests/`: `moveItem`, pointer-y → index, the
  preview controller, and the additive merge.

`pytest` runs with `filterwarnings = ["error"]`; ruff line length 100; ffmpeg
must be on PATH. YOLO is never run in tests and nothing here touches detection.

## 9. Out of scope

- **The cross-session rally browser.** It is a filter UI over one session's
  rallies until a second session exists. Worth building then, not now.
- **Reclaim Space.** Not deferred — rejected. The external drive holds ~110
  hours of play with every original kept, so deleting a 5.6 GB original buys
  capacity that is not scarce and forfeits the 4K source for every rally that
  was not flagged at the time. See Retention in the core design doc.
- **Per-reel trim overrides.** Rally bounds are the bounds; fixing a bad cut
  once should improve every reel that uses it.
- **Music, titles, transitions**, and vertical export — excluded by the
  original design and still excluded.
- **Chaining clip files for preview**, superseded by §6.5.
