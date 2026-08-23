# SplitStep — Rally Notes and Burned-In Captions

**Date:** 2026-08-21
**Status:** Approved, ready for implementation
**Extends:** `docs/superpowers/specs/2026-08-21-clip-export-and-reels-design.md` §4
**Depends on:** the reels builder landing first, for §7's phase 2 only

---

## 1. Problem

A rally carries three verdicts — starred, point, rejected — and nothing else.
Every judgement past those three lives in the reviewer's head and is gone by
the next session. "Late on the backhand" is the kind of thing you notice once,
while watching, and cannot reconstruct afterwards from a 7-second clip that
looks like forty others.

Exported clips have the matching problem from the other end. `clips/` fills
with files named by span — `01-738000-745000.mp4` — which is exactly right for
identifying them mechanically and useless for telling one point from another
by eye.

One free-text note per rally solves both: typed during review, burned into the
clip on export.

## 2. Scope

**In:** a note per rally, written in queue mode; carried across a re-segment;
burned into the bottom-left of the 4K clip at export.

**Out:** per-reel title cards, note search across sessions, tags or structured
fields, notes on anything other than a rally. The note is one string. If it
later wants structure, that is a separate spec.

## 3. Capture

`N` in queue mode opens a one-line field beneath the video, autofocused, with
the clip still looping behind it. Enter commits, Escape cancels. The field is
seeded with the rally's current note, so `N` is edit as well as create.

Queue mode's window-level `keydown` handler already returns early for an
editable target (`isEditableTarget`, `web/src/lib/keyboard.ts`), and
`tests/editable-target-guard.test.ts` pins that a space typed into a
co-mounted field does not reach `case ' '`. Notes inherit that guard rather
than adding a second mechanism; the test file gains a case for this field.

The note is capped at 120 characters. That is the length that still fits two
rendered lines inside the caption pill at 4K without shrinking the type, and
the cap is enforced in `web/src/lib/notes.ts` so the limit is one number in one
pure module rather than a `maxlength` attribute the API does not know about.
The route enforces it too, rejecting a longer note with a 400: the UI is not
the only writer a library ever has, and a note that cannot be rendered must not
reach the database.

A rally that has a note shows `✎` in the metadata line beside its star and
point indicators — the reviewer needs to know a note exists without opening the
field to find out.

All of the logic — trimming, the cap, whether the buffer differs from what the
server holds — lives in `web/src/lib/notes.ts`. `QueueMode.svelte` renders it.
This is the same split the rest of the frontend follows and the reason the
queue's behaviour is testable at all under jsdom.

## 4. Storage

Migration `006_rally_notes.sql`:

```sql
ALTER TABLE rallies ADD COLUMN note TEXT NOT NULL DEFAULT '';
```

`replace_rallies` carries `note` across a re-segment by the same >50% overlap
rule it already applies to `starred`, `point` and `rejected`, and for the same
reason: a threshold sweep that silently discarded the reviewer's written
judgements would be the exact failure the star carry-over exists to prevent.

Two consequences worth stating plainly, because both are chosen:

A note is lost when a re-segment produces no candidate overlapping its rally by
more than half — a rally that a higher threshold stops detecting takes its note
with it. This matches what already happens to a star on that rally. The
alternative (an append-only corpus keyed to `(source_id, det_start_ms,
det_end_ms)`, as `rally_labels` does) was considered and rejected: notes are
about the clip you cut, not about the detector's judgement, and anchoring them
to a span the reviewer may have since dragged elsewhere would re-attach a note
to bounds it was never written about.

A note is *not* a label. `rally_labels` is the human-judgement corpus that
tuning is scored against, and it survives re-segmentation precisely because
scoring needs it to. A note has no verdict in it and never reaches the scorer.

## 5. Burn-in

### 5.1 Why not `drawtext`

The obvious filter is unavailable. The ffmpeg on this machine (Homebrew 9.0.1)
is built without `libfreetype` and without `libass`, so neither `drawtext` nor
`subtitles` exists in its 489 filters. Requiring a rebuild would make a clip's
appearance depend on how each machine's ffmpeg was compiled — the same class of
portability problem that "software libx264, never a hardware encoder" exists to
prevent, and a worse one, since a caption that silently fails to render leaves
no artifact to notice.

### 5.2 What happens instead

`splitstep/media/caption.py`:

```python
def render_caption(text: str, width: int, height: int) -> Image.Image | None
```

Pillow (12.3.0, already in the environment via YOLO) renders the caption to a
transparent RGBA image the size of the final frame: white text on a rounded
dark pill, bottom-left, inside a safe margin. Returns `None` for empty text, so
"no note" is a `None` at the top of the pipeline rather than a conditional
threaded through the filter chain.

Being pure Pillow makes it unit-testable with no ffmpeg at all: wrapping, the
two-line cap, the empty case, and font fallback are all assertions about an
image object.

Bottom-left is not arbitrary. On the one camera position this library has,
play sits mid-frame and the bottom strip is empty court, so a caption there
covers nothing. It was chosen against three alternatives — bare shadowed text,
a full-width lower-third gradient, and a top-left placement — by rendering all
four over a real 4K frame from source 01 and looking at them. The pill won on
legibility over arbitrary footage: the gradient dims the whole bottom strip
including any near-court action, and unboxed text depends on what happens to be
behind it.

The font is resolved from a fallback list at render time, since nothing in this
repo ships a font file. First hit wins; the list ends at Pillow's built-in
bitmap font so a missing system font degrades to ugly rather than to a crash.

### 5.3 The filter chain

`make_clip` takes `caption_png: Path | None`. When set, the PNG becomes a
second input and an `overlay` is appended to the existing chain — after
`scale`/`pad`, so the caption's coordinates are in final 4K frame space and
never get scaled or letterboxed with the picture, and before `setsar=1`, which
keeps its position as the documented last filter.

Nothing about the encode changes: same profile, same libx264, same headers.
A captioned clip stays `-c copy` concat-compatible with an uncaptioned one,
which is what lets the reel builder concatenate them without knowing captions
exist.

### 5.4 Reels get captions for free

Because burn-in happens when the clip is cut, a reel assembled by `-c copy`
concat carries every caption already. The reel builder needs no awareness of
this feature.

## 6. Staleness, and why there is no `clip_note` column

Export is incremental by construction: `plan_export` asks whether the file a
rally's *current bounds* imply exists on disk, and `find_orphan_clips` asks the
inverse. Neither reads `clip_path`. `clip_relpath`'s docstring states the
principle — "There is no separate staleness record to keep in sync, which is
why this is a pure function and not a database column."

A caption changes the file's contents, so it belongs in the file's identity:

```
no note    01-738000-745000.mp4          (unchanged)
with note  01-738000-745000-c1f4a9e02.mp4
```

The suffix is the first 8 hex characters of the SHA-256 of the note's UTF-8
bytes, present only when there is a note. Named explicitly because the value
ends up in filenames on disk: it has to be reproducible across machines and
Python versions, which `hash()` is not. Every clip cut before this feature keeps resolving to its
existing path, so nothing re-cuts on the strength of the migration alone.

What this buys, all of it from mechanisms that already exist:

- Edit a note, and the rally implies a path that is missing, so the next
  export re-cuts it. No staleness flag anywhere.
- The old file stops being claimed by any rally, so `find_orphan_clips`
  reports it and `splitstep clips sweep` deletes it — the same handling a moved
  boundary already gets.
- An orphan stays self-identifying: `parse_clip_name` gains one optional group
  and still reads source index and span straight off the name.

The cost, accepted: a note edited after export strands a 4K file until the
sweep runs, and clip names are less tidy than they were.

## 7. Sequencing

Another agent is building the reels builder, which owns `export.py` and the
clip pipeline for the duration. This spec is therefore implemented in two
phases with a hard boundary between them.

**Phase 1 — capture and storage.** Migration `006`, `POST /api/rallies/{id}/note`,
the `note` field on the rally type, the queue-mode field and `✎` indicator,
`lib/notes.ts`, and the `replace_rallies` carry-over. Touches `splitstep/db/`,
`splitstep/api/`, `web/`. Touches none of `export.py`, `jobs/handlers.py`,
`media/transcode.py`, `media/clips.py`. Can land immediately.

**Phase 2 — burn-in.** `media/caption.py`, the `caption_png` parameter on
`make_clip`, the caption suffix in `clip_relpath`/`parse_clip_name`, and
`handle_clip` rendering from the rally's note. Waits for the reels branch to
merge, then rebases onto it.

Phase 1 is useful on its own: notes are worth having in the review UI whether
or not they ever reach a clip.

## 8. Testing

**Python.** `render_caption` unit tests (empty input, wrapping, the two-line
cap, font fallback) assert on the returned image and need no ffmpeg. The
filter-chain assembly test extends to cover overlay placement relative to
`pad` and `setsar`. `parse_clip_name` round-trips both name forms.
`replace_rallies` gains a note carry-over case beside the existing star and
point ones, including the >50%-overlap boundary. `plan_export` gains a case
where a changed note re-lists a rally as pending.

**Frontend.** `web/tests/notes.test.ts` covers `lib/notes.ts` — trimming, the
120-character cap, dirty detection. `editable-target-guard.test.ts` gains a
case for the note field, since it is a new editable target co-mounted with the
queue's key handler.

**By hand.** That the caption is legible at 4K on real footage, and that a
captioned and an uncaptioned clip concat cleanly with `-c copy`.

## 9. Rejected alternatives

**A sidecar `.srt` per clip.** Never re-encodes and stays editable forever, but
a soft subtitle track does not survive `-c copy` concat into a reel, and most
players will not show it. The request was for text on the screen.

**Two fields, private note and public caption.** Considered and dropped as
YAGNI. One field, and what you type is what gets burned in.

**A caption that fades after a few seconds.** Deferred, not rejected. It costs
an `enable=` expression and a fade, and nothing yet says the caption is in the
way for the whole clip.
