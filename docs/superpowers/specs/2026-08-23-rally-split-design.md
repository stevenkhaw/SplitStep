# SplitStep — Splitting a Rally in Two

**Date:** 2026-08-23
**Status:** Approved and implemented — migration 009, `web/src/lib/split.ts`,
and `C`/`U` in timeline mode. Follow-ups in
`plans/2026-08-23-rally-split-followups.md` remain open.
**Extends:** `docs/superpowers/specs/2026-08-20-review-ux-design.md` (timeline mode)
**Touches:** `docs/superpowers/specs/2026-08-21-rally-labelling-design.md` (the corpus anchor)

---

## 1. Problem

Detection is recall-biased on purpose, and one shape of that bias has no
remedy in the UI: two rallies separated by a break too short to score below
threshold come out as a single interval. The detector is not wrong to merge
them — a two-second gap between points looks like a two-second gap inside a
point, and the alternative tuning shreds real rallies. The merge is the
correct trade.

What is missing is the human's ability to disagree afterwards. Timeline mode
can move a rally's two boundaries and nothing else, so a merged pair can only
be trimmed down to one of its halves, throwing the other away, or kept as one
oversized clip. There is no third rally to move a boundary *to*.

One cut at the playhead turns that dead end into a ten-millisecond keystroke.

## 2. Scope

**In:** `C` in timeline mode cuts the current rally in two at the playhead.
`U` merges a hand-made half back into the rally it came from. A rally with no
detector provenance is representable in the schema and understood by every
consumer of it.

**Out:** merging two *detector* rallies into one; splitting into three or more
in a single action; splitting from queue mode or label mode; splitting with two
cut points to discard the middle; surviving a re-segment. Each of these is a
separate decision and none is needed to fix the problem above.

The break between the two halves rides inside whichever half it lands in and
is trimmed off afterwards with the existing `[` and `]`. That is one keystroke
plus a trim the reviewer already knows, against a new two-point modal
interaction — and the trim is one they may want to skip entirely, since a
short lead-out is not a defect in a clip.

## 3. The absent det span

The load-bearing decision. `rally_labels` anchors on
`(source_id, det_start_ms, det_end_ms)` — the detector's own span, not a rally
row, which is what lets the corpus survive `replace_rallies`. Two halves that
both inherit their parent's det span therefore become **the same row in the
corpus**, and labelling the second silently overwrites the judgement on the
first. That is the exact class of quiet data loss migration `007` and
`rally_labels`' missing foreign key exist to prevent.

Nor can each half be given its own det span covering its own bounds. `det_*`
is immutable and records *what the detector originally guessed*; writing
spans the detector never proposed into it fabricates training data and feeds
`labels score` candidates that have no basis in any detector run.

So: **half one keeps the det span, half two has none.**

```
rally 33   det = (2900, 41200)   bounds = 2900 → 22000    labellable
rally 34   det = NULL            bounds = 22000 → 41200   human-made
```

`det_* IS NULL` becomes the single, self-describing marker for "a human made
this rally; the detector never proposed it." Not a boolean flag beside the
columns it describes — a flag can drift out of agreement with them, the
absence of the span cannot. Every consumer that needs to ask "is this a
detector proposal?" already reads `det_*`, and now gets a truthful answer
without knowing splits exist.

The cost is that half two can never be labelled. That is not a gap; it is the
existing contract stated out loud. `splitstep labels score` reports
`span recall (labelled spans only)` precisely because it *cannot see play the
detector never proposed*, and a hand-cut half is the purest example of such a
span. Letting it into the corpus would inflate a metric that is already
carefully named to avoid implying coverage it lacks.

## 4. Storage

Migration `009_rally_split.sql`. SQLite cannot drop `NOT NULL` in place, so
this is a table rebuild — create, copy, drop, rename — the same shape as `007`.

The rebuild is safe to do on `rallies` specifically: nothing in the schema
declares `REFERENCES rallies`. `reel_items` keys on the span (migration `007`),
and `rally_labels.rally_id` deliberately carries no foreign key (migration
`003`). Verified against the live library before writing this.

Abridged — the rebuild restates every column of `001`+`005`+`006`+`008`
verbatim; only the two `det_*` lines and the new `CHECK` differ:

```sql
-- det_start_ms/det_end_ms become nullable. NULL means "no detector ever
-- proposed this rally" -- see §3.
CREATE TABLE rallies_new (
  -- ... id, session_id, source_id, idx, start_ms, end_ms unchanged ...
  det_start_ms  INTEGER,
  det_end_ms    INTEGER,
  -- ... confidence, starred, rejected, point, reviewed_at, clip_path,
  --     note, seen_at unchanged ...
  -- Both or neither, never one. A half-present det span would be a third
  -- state nothing knows how to read: `det_start_ms IS NULL` is the question
  -- every consumer asks, and it must answer for the pair.
  CHECK ((det_start_ms IS NULL) = (det_end_ms IS NULL)),
  UNIQUE(session_id, idx)
);
INSERT INTO rallies_new SELECT ... FROM rallies;
DROP TABLE rallies;
ALTER TABLE rallies_new RENAME TO rallies;
-- indexes recreated: idx_rallies_session, idx_rallies_source,
-- idx_rallies_starred, idx_rallies_point
```

Existing rows all carry real det spans and migrate unchanged.

## 5. `splitstep/db/rallies.py`

Two functions, each one transaction, each ending in the existing
`_renumber(conn, session_id)` — the two-phase negative-placeholder renumber
that already exists to keep `UNIQUE(session_id, idx)` satisfied while rows
shuffle. Neither reimplements it.

```python
def split_rally(conn, rally_id, at_ms) -> str          # returns the new id
def merge_into_previous(conn, rally_id) -> None
```

### 5.1 `split_rally`

Refuses unless `start_ms < at_ms < end_ms`, strictly — two non-empty halves.

The minimum-length floor (`MIN_RALLY_MS`) is deliberately **not** enforced
here, and that is a deliberate match to how `/bounds` already behaves rather
than an omission. `BoundsBody` validates only `end_ms > start_ms`; the floor
lives in `clampMinGap` client-side. `media/concat.py` states the reason
outright: "nothing else in the app enforces it on a span a reel can hold... A
hand-trimmed clip well under 1.5s is a real reel input." A server-side floor on
split would be the only place in the app that second-guesses a reviewer about
how short a clip may be, and it would do so on the one operation where a very
short lead-in half is a legitimate thing to want.

So the two layers divide the same way `/bounds` does: the server rejects what
is *incoherent* (a zero-length half), the client discourages what is merely
*tiny* (`canSplit`, §7.1).

| column | half 1 (the existing row) | half 2 (new row) |
| --- | --- | --- |
| `start_ms` / `end_ms` | `start` → `at_ms` | `at_ms` → `end` |
| `det_start_ms` / `det_end_ms` | unchanged | **NULL** |
| `starred` `rejected` `point` `note` | unchanged | inherited |
| `confidence` | unchanged | inherited |
| `seen_at` `reviewed_at` | unchanged | inherited |
| `clip_path` | **NULL** | **NULL** |

Flag inheritance is not a fresh judgement call. It is what `replace_rallies`'
own carry-over rule produces for these two intervals: `overlap_fraction`
divides by the *shorter* span, so each half sits fully inside the parent and
scores a flat `1.0`, clearing `STAR_OVERLAP_MIN` outright. Splitting therefore
leaves the rally set in exactly the state it would hold if the detector had
proposed both intervals from the start. Any other choice would make a split
rally behave differently from a detected one for no reason a reviewer could
predict.

`clip_path` is the deliberate exception, and for the same reason
`_carried_clip_path` demands an *exact* span match rather than an overlap: the
4K file on disk was cut at the old span and matches neither half. Nulling both
is the truthful record. The orphaned file is `clips prune`'s problem, which is
exactly the case `set_clip_path`'s docstring names as that command's purpose.

### 5.2 `merge_into_previous`

Refuses unless **all** of:

- the target's `det_start_ms IS NULL`,
- a previous rally exists in the same source,
- that previous rally's `end_ms` equals the target's `start_ms`.

Then: extend the previous rally's `end_ms` to the target's, delete the target,
renumber. The previous rally's own `det_*` is untouched — merging back does
not restore provenance to a rally that never lost it, and does not invent it
for one that never had it. Its `clip_path` is nulled for the same reason
`split_rally` nulls it: the row's span just changed, so any file cut at the
old span no longer describes it. Every other column on the previous rally is
left alone; the target's flags are discarded rather than merged, since they
were inherited copies of the previous rally's own in the first place.

### 5.3 Splitting a hand-made half again

Permitted, and falls out of the rules rather than needing a case of its own.
`split_rally` never reads `det_*`, so cutting half two yields two det-less
rows. `merge_into_previous` tests the *target's* det span, not the previous
rally's, so the halves collapse back in reverse order: the second merges into
the first, and the first then merges into the det-bearing original. The guard
still holds at every step — the one row carrying provenance is never a legal
merge target.

The det-less guard is the whole safety story. Merge can only ever undo
something a human made in this session; it can never delete a row the label
corpus is anchored to. This is why the inverse of split is not "merge any two
adjacent rallies" — that more useful-sounding operation would let a keystroke
destroy detector provenance, and fixing detector *over*-segmentation is a
different feature with a different risk profile.

## 6. API

```
POST /api/rallies/{id}/split   {"at_ms": 22000}   → {"ok": true, "new_rally_id": "..."}
POST /api/rallies/{id}/merge                      → {"ok": true}
```

Both 404 on an unknown rally and 400 on a refused precondition, naming which
one failed — the routes are as thin as every other write route and defer
validation to §5.

### 6.1 The required change to `/bounds`

`POST /api/rallies/{id}/bounds` currently calls `record_boundary_correction`
on *every* drag, reading `det_start_ms`/`det_end_ms` to anchor the corpus row.
On a det-less half those are NULL. The route must skip the corpus write
entirely and call only `set_bounds`; `_rally_det_span` returns `None` rather
than a row of NULLs, and `/label` skips on the same test.

Without this, the first boundary drag on a split half either raises or writes
a corpus row anchored to `(NULL, NULL)` — a key nothing can ever resolve
against, quietly accumulating in the append-only table.

This is a behaviour change to an existing route and needs its own test, not
just coverage-by-accident from a split test.

## 7. Frontend

### 7.1 `web/src/lib/split.ts`

All four functions pure, per the standing rule that logic lives in `lib/` and
components are thin shells — jsdom has no `<video>`, so anything inside
`TimelineMode.svelte` is verified by hand and nothing else.

```ts
canSplit(rally, atMs): boolean
canMerge(rally, prev): boolean
applySplit(rallies, rallyId, atMs, newId): Rally[]
applyMerge(rallies, rallyId): Rally[]
```

`applySplit` and `applyMerge` update the list **optimistically in place**
rather than bumping Session's `rallyRevision` to force a remount. That is a
UX requirement, not an optimisation: a remount resets the playhead, so a cut
at 22.0s would throw the reviewer back to the top of the rally at the precise
moment they want to be trimming the seam they just made.

The local renumber mirrors `_renumber`'s ordering — `sources.idx`, then
`start_ms` — and a test asserts client and server assign identical `idx` for
the same input. Two implementations of one rule is a real cost; the
alternative is a remount on every cut, and the rule is four lines and frozen
by migration `001`'s `UNIQUE(session_id, idx)`.

No new callback to Session is needed. `closeTimeline` already refetches the
session and bumps `rallyRevision` on every exit from timeline mode — it was
written for bounds edits, and a split is the same class of change. The queue
therefore picks up both halves on `Esc` with no wiring at all.

`types.ts` widens `det_start_ms` and `det_end_ms` to `number | null`.

### 7.2 Keys

A new `Split` group in `shortcuts.ts`' `TIMELINE` table:

- `C` — Split here into two rallies
- `U` — Merge back into the previous

Both are unbound in every mode today. `C` rather than `S`: `S` is *star* in
queue mode, the reviewer crosses between the two modes constantly, and a
reflex `S` in timeline that cut instead of starred is the worst possible
misfire for a key whose inverse is conditional. `shortcuts.test.ts` already
pins that no key is bound twice within a mode, so this stays honest for free.

The inline strip caps at six or it wraps and stops being glanceable. The
frame-step pair `,`/`.` leaves the strip for the `?` overlay — they are a
mirror pair, discoverable from one another and from the overlay:

```
[ in    ] out    C split    U merge    Esc back    ? keys
```

Trim, split, leave. A tighter grouping than the one it replaces.

`U` on a rally `canMerge` rejects surfaces the refusal in the toaster, naming
which of the two conditions failed — a detector rally, or nothing abutting its
start. That is the pattern `applyEdit` already established for `[` and `]`:
"Refusals are surfaced, never silent." A greyed key hint would be nicer still,
but `KeyHints` renders `primaryShortcuts(mode)` as static data with no
per-rally state, and adding a disabled-set prop for one key is more mechanism
than the toast is worth.

## 8. Fallout

### 8.1 Re-segment

Splits are lost on a re-segment, exactly as hand-dragged boundaries are.
`replace_rallies` deletes every rally for the source and rebuilds from
detector intervals; a hand-made rally has no interval to be rebuilt from.
This is consistent rather than convenient — persisting split points in their
own table and re-applying them after each sweep is a coherent design, and it
is out of scope here.

`editedBoundaryCount` compares `r.start_ms !== r.det_start_ms`. Against a NULL
det span that comparison is always true, so split halves would be counted and
then described with the wrong noun — "hand-edited boundaries" for rallies
whose boundaries were never edited. Add:

```ts
splitCount(rallies, sourceId): number     // det-less rallies on this source
```

exclude det-less rallies from `editedBoundaryCount`, and widen
`resegmentConfirmMessage` to name both losses separately with correct
singular/plural. The confirmation's whole purpose is to state the cost
accurately before it is paid.

### 8.2 Label mode

A det-less rally has nothing to anchor a corpus row to, so `LabelController`
filters it out **in its constructor**, alongside the existing decision not to
filter by `rejected`. Filtering at construction rather than skipping during
`next()`/`back()` is what keeps `index` and `total` truthful: the "12 / 121"
counter must not promise judgements that can never be made. `jumpTo` already
no-ops on an id it cannot find, so a `startAtRallyId` naming a det-less half
lands on index 0 rather than erroring.

### 8.3 Reels

No work. A reel item pinned to the pre-split span stops matching any rally and
becomes an **orphan** — already a designed state, not a failure: badged in the
builder, still playable, cuttable and renderable, and `handle_clip` already
treats `rally_id` as optional precisely so this case works.

## 9. Testing

**Python** — `tests/test_split.py`:

- split arithmetic, and refusal at both `MIN_RALLY_MS` edges
- half two carries NULL det, half one's det is untouched
- flag inheritance matches what `replace_rallies`' overlap rule would give
- `clip_path` nulled on both halves
- `idx` correct across a two-source session (the case `_renumber`'s two-phase
  dance exists for)
- merge refuses a rally carrying a det span
- merge refuses a non-adjacent pair, and a previous rally in another source
- rollback leaves the rally set untouched on failure

`tests/test_labels.py` gains: a boundary drag on a det-less rally writes no
corpus row. `tests/test_migrations.py` gains: `det_*` nullable, and the
both-or-neither `CHECK` rejects a half-present span.

**Web** — `web/tests/split.test.ts` for the four pure functions, including
client/server `idx` parity against the same fixture the Python renumber test
uses. `web/tests/resegment.test.ts` gains the split-versus-boundary count
distinction.

## 10. Note on rollout

A re-segment wipes every split (§8.1). Source `02` of session `2026-08-18` is
mid re-detect as this spec is written, so the first rally set this feature
will be exercised against is the one that detect produces — not the 121
rallies currently on screen.
