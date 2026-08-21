# BootlegVision — Clip Export and Reels

**Date:** 2026-08-21
**Status:** Approved, ready for implementation
**Extends:** `docs/superpowers/specs/2026-08-19-bootlegvision-design.md` §7
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
video out of the library. `bootleg` has never cut a clip.

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
`2026-08-19-bootlegvision-design.md`. That document predates migration `002`,
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
is still set on completion — it is what Reclaim Space will check later.

Export is therefore incremental by construction: exporting a set enqueues jobs
only for spans with no clip on disk. Re-exporting after marking three more
points costs three encodes, not twenty-four.

### 4.2 The locked profile

```
mp4 · H.264 High · yuv420p · 3840×2160 · 30 fps CFR · CRF 20 · AAC 128k 48 kHz stereo
```

**Changing this breaks `-c copy` against every clip ever cut.** It is locked in
the original design and stays locked here.

Clips are software-encoded (libx264, `-preset medium`) on purpose. Hardware
encoders emit vendor-specific SPS/PPS headers, so a clip cut on the Mac and one
cut on the 4070Ti would fail to concat cleanly or concat with artifacts.
libx264 produces identical headers on every machine, permanently. Roughly 30 s
per 20-second clip; the whole first session is ~9 minutes.

Sources that do not match are conformed at cut time rather than at concat time:
sub-4K is upscaled and padded, 60 fps is decimated, and 29.964 fps — the first
real source — is conformed to 30 CFR at a cost of ~0.12% duplicated frames. A
mixed-parameter clip library cannot be concatenated with `-c copy`, and that
guarantee is worth more than avoiding an upscale.

### 4.3 The `clip` job

Registered in `HANDLERS`, idempotent (overwrites its output).

```
-noautorotate  -ss <start_ms> -i <src> -t <duration_ms>
-vf  <rotation_filter(rotation_deg)>,
     scale=3840:2160:force_original_aspect_ratio=decrease,
     pad=3840:2160:(ow-iw)/2:(oh-ih)/2
-r 30
-c:v libx264 -profile:v high -pix_fmt yuv420p -crf 20 -preset medium
-c:a aac -b:a 128k -ar 48000 -ac 2
-movflags +faststart
```

Four things are load-bearing in that argument order:

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

Source is `original.*` (via `find_original`) when `sources.has_original`, else
`proxy.mp4`, which the same scale/pad chain upscales to the locked profile. A
clip cut from the proxy is flagged 1080p-sourced in the UI.

Free space is checked before the job starts. A 4K clip that dies at 90% is
worse than a job that refuses to run.

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
- **Reclaim Space.** It depends on clips existing, which is what this builds,
  but deleting a 5.6 GB original is a separate decision with its own guard
  (refuse while any starred rally from that source lacks a clip).
- **Per-reel trim overrides.** Rally bounds are the bounds; fixing a bad cut
  once should improve every reel that uses it.
- **Music, titles, transitions**, and vertical export — excluded by the
  original design and still excluded.
- **Chaining clip files for preview**, superseded by §6.5.
