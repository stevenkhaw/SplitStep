# Inherited labels, and adding a rally by hand

**Date:** 2026-09-19
**Status:** design, approved in chat 2026-09-19

Two changes that share no code and one motivation: after a re-segment, the
reviewer's own work stops being visible, and there is no way to tell the app
about play it never proposed.

---

## Part 1 — A judgement should survive the span moving

### The measurement

On the live library, 2026-09-16 source 01:

| source | labelled spans | still resolve | stranded |
|---|---|---|---|
| 2026-09-16 src 1 | 44 | **6** | **38** |
| 2026-08-18 src 1 | 27 | 27 | 0 |
| 2026-09-07 src 1 | 6 | 6 | 0 |

The sources showing 0 stranded were not re-segmented after labelling. The one
that was lost the display of 38 of its 44 judgements, which is what "some were
already labelled and some were not" looks like from the reviewer's chair.

### What is actually wrong

Nothing in the database. `rally_labels` anchors on
`(source_id, det_start_ms, det_end_ms)` — the detector's *own* guess — with no
foreign key, precisely so `replace_rallies` cannot wipe it. All 169 rows are
intact and `splitstep labels score` still reads every one. CLAUDE.md's claim
that the corpus survives a re-segment is true about the data.

What does not survive is the *lookup*. `LabelController`'s constructor
(`web/src/lib/labels.ts`) builds `bySpan` keyed on the exact triple and reads it
back with the rally's current `det_start_ms`/`det_end_ms`. A re-segment produces
new detector guesses, so the new rallies carry new anchors and the old
judgements match nothing.

This is a read-time bug in one pure function, not a schema problem.

### The change

`LabelController` gains a second resolution step. Exact match first, unchanged.
On a miss, fall back to the best-overlapping labelled span for the same source,
using `overlap_fraction`'s existing `>= 0.5` rule — the same rule
`replace_rallies` uses to carry a star and the same rule `labels score` uses to
match a candidate to a labelled span. One definition of "the same rally"
already exists and is public for exactly this reason; this adds a third caller,
not a fourth rule.

A record resolved by the fallback is marked **inherited**. The distinction is
carried in the controller's output, not written anywhere.

**Nothing is written on re-segment.** The rejected alternative was to re-anchor
rows onto the new det spans during `replace_rallies`. That asserts the reviewer
judged a span they never saw, which is the fabricated-training-data objection
CLAUDE.md already makes about inventing det spans. Read-time resolution
fabricates nothing and is undone by reverting one function.

### Where an inherited verdict may be shown

**Label mode only.**

It must never reach the audit route. That pass is deliberately blind — the
`Window` it returns carries nothing but its span, and the 2026-08-20 pass
mislabelled a clip which only the blindness exposed. An inherited verdict
rendered there would bias the one measurement in the project that can see
recall. `Audit.svelte` calls `api.sourceLabels` directly and does not go through
`LabelController`, so the separation is structural rather than a flag anyone has
to remember — the plan must keep it that way.

### Confirming an inherited verdict

When the reviewer presses a verdict key on a rally showing an inherited label,
the write is an ordinary label write against that rally's **current** det span.
The judgement stops being inherited, and the next re-segment starts from a clean
exact match. No special case in the writer: `LabelWriter` already sends the
span's whole state, and the span it sends is the rally's own.

An inherited verdict that is never confirmed is never written. The corpus keeps
recording only spans a human actually looked at.

---

## Part 2 — Adding a rally by hand

### Why

The detector cannot propose what it never scored, and pair mode scores a hard
0.000 whenever one player is out of frame (see
`2026-09-18-first-measured-recall.md`). Play the detector missed is currently
unreachable: `split` cuts an existing rally in two and `merge` undoes that, but
no route creates a rally at an arbitrary span.

### The data model already supports it

A rally with `det_start_ms IS NULL` **is** the "made by a human, not proposed by
the detector" marker, and `split` already creates them. A manually added rally
is the same kind of row and inherits every documented consequence for free:
`merge_into_previous` will accept it, `/label` and `/label/retract` refuse it,
`LabelController` filters it out so `index`/`total` stay truthful, and
`editedBoundaryCount` excludes it in favour of `splitCount`. A re-segment
destroys it, like every other manual edit.

No migration. No new column. No boolean beside `det_start_ms` — the absence is
the marker, for the reason CLAUDE.md gives: a boolean drifts out of agreement
with the columns it describes.

### Shape

- **Lives in TimelineMode**, as a mode within it. Timeline already owns boundary
  editing, the overview band, `VideoDeck`, and the `C`-to-split key. Adding a
  span is the same family of work and reuses all of it.
- **`N` starts an add.** Free in timeline's keymap (`[ ] C U Space , . Esc ?`).
  `[` and `]` then set in and out exactly as they already do, `Enter` commits,
  `Esc` cancels. No new in/out vocabulary — reusing the existing keys is the
  whole reason the mode belongs here.
- **A full-source scrub bar**, spanning the whole source rather than one rally's
  span. This is the only genuinely new surface; `VideoDeck` currently plays
  between in and out points and has no way to reach the rest of the file.
- **The new rally starts plain**: `det_start_ms` and `det_end_ms` NULL, not
  starred, not a point, not rejected, no note. Flagging it is the same
  keystrokes as any other rally, and a default that asserted "this is a point"
  would be a claim nobody made.
- **Overlap with an existing rally is allowed.** Nothing forbids it today and
  the rally list is not a partition. `clip_relpath` is span-derived so two
  overlapping rallies name two different files, and score replay orders by
  `idx`, which `_renumber` assigns regardless.
- **No auto-cut.** Adding a span never queues a clip job, consistent with
  "never auto-enqueues the cuts". Cutting stays the existing explicit button.

### Server side

One new route, `POST /api/sources/{source_id}/rallies`, taking `start_ms` and
`end_ms`. Validation mirrors the existing bounds rules: both inside the source's
duration, `end_ms - start_ms >= MIN_RALLY_MS`. It inserts one rally with NULL
det bounds and renumbers, reusing whatever `split` already does rather than
growing a second insert path.

A CLI twin is out of scope. Unlike setup and scoring — where HTTP and terminal
both had to validate and drift was the risk — nothing about picking a span by
eye is expressible in a terminal.

---

## Testing

Part 1 is pure TypeScript in `lib/`, so `web/tests/labels.test.ts` covers it
directly: exact match still wins, a shifted span resolves by overlap and is
marked inherited, a span under 50% resolves to nothing, and a hand-made rally
(null det bounds) is still filtered out. One test must assert that `Audit.svelte`
does not route through `LabelController`.

Part 2 splits: span validation and the insert are Python, covered in
`tests/test_api_rallies.py` alongside the existing split and merge tests; the
scrub-bar geometry is pure TypeScript and belongs in `lib/` with the rest of the
timeline math, not in the component. The component stays a thin shell and is
verified by hand, because jsdom has no `<video>`.
