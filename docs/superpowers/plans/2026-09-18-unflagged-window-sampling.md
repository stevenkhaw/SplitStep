# Sampling Unflagged Windows — Closing the Recall Hole

**Date:** 2026-09-18
**Prompted by:** 2026-09-16 source 01. The first point starts at 0:25; the
detector's first interval is 3:06. Pair mode scores 0:25 a hard **0.000**, so
no threshold recovers it and no re-segment can.
**Required reading:** `2026-08-20-camera-viewpoint-validation.md` (and its
2026-08-21 correction), `2026-08-27-gate0-fence-mount-first-look.md`. Nothing
here changes a tuning constant, and this plan is not a licence to.

---

## Why the corpus cannot see this miss

`rally_labels` anchors on `(source_id, span_start_ms, span_end_ms)`, and every
writer today reaches it through a rally: `POST /api/rallies/{id}/label`, with
`LabelController` filtering its list to rallies carrying a detector span. A
window the detector never proposed has no rally, so there is nothing to press
`L` on. Hand-cutting one with `C` does not help — a split rally carries
`det_start_ms IS NULL` and is filtered out by construction.

The validation doc says so in its last line:

> every label attaches to a span the detector proposed, so this corpus
> measures precision and boundary error, never recall over play the detector
> missed

The 2026-08-21 pass got its six unflagged windows from `web/public/label.html`,
a 112-line throwaway with the window list pasted in as a literal. It was
deleted when label mode shipped, and label mode replaced only the flagged half.

## What this is not

Not a new table, and not a new label shape. `add_label` already takes
`source_id` + a span with `rally_id` optional — the storage layer was built
span-addressed from the start (003's comment says the anchor is the span, "not
a row"). Only the *routes into it* assume a rally.

## The pieces

### 1. `splitstep/label_sample.py` — a pure, seeded sampler

```
sample_windows(*, duration_ms, intervals, n, window_ms, seed) -> list[Window]
```

Half the windows centred on detector intervals, half drawn from the gaps
between them. Deterministic in `seed`, so the same sample can be recomputed
from nothing — no table, no migration, and a reviewer can resume a sitting
after a reload and get the same list back.

Returned **shuffled**, and carrying no field saying which half a window came
from. That is the point: the 2026-08-20 doc's own labelling error was caught
because the tool hid the detector's guesses, and its 2026-08-21 pass repeated
that deliberately. A `flagged: true` in the payload would re-anchor the
reviewer to the thing being measured.

Windows never overlap each other, so two labels can never disagree about the
same footage.

### 2. `GET /api/sources/{id}/label-sample?n=&seed=&window_ms=`

Recomputed per request from the source's duration and its current rallies'
detector spans. Cheap, stateless.

### 3. `POST /api/sources/{id}/label` — span-addressed write

What `/api/rallies/{id}/label` does, minus the rally: body carries
`span_start_ms`, `span_end_ms`, `verdict`, `flags`. Writes `rally_id = NULL`.
Retraction follows the existing route's shape so `latest_labels` resolves
sampled and rally-anchored rows by exactly the same rule.

### 4. An audit route in the app, `#/audit/<source_id>`

Its own route, not a mode inside Session: the session route renders a rally
list, an overview band and a score board, all of which tell the reviewer what
the detector thought. Blind means blind. Reuses `VideoDeck` between in/out
points, the verdict keys already in `shortcuts.ts`, and a `k / n` counter.
Logic in `web/src/lib/labelsample.ts`; the component stays a shell.

### 5. `splitstep labels score` learns to count misses

The payoff, and the reason 1-4 exist. Its recall figure is currently named
`span recall (labelled spans only)` precisely because it cannot see play the
detector never proposed. With unflagged windows in the corpus it can: a
labelled window carrying a play verdict that no candidate interval overlaps is
a miss. The existing figure keeps its name and meaning; the new one is
reported beside it, named for what it measures.

## What this still does not measure

A sample is a sample. Fifteen windows was small in 2026-08-21 and twenty is
not much better — the doc's own warning that "anything that separates cleanly
on it should be re-checked against a second labelling pass" applies to every
number this produces. What changes is only that recall becomes measurable at
all, which it currently is not.

---

## What a rally's start means (2026-09-18, Steven)

Stated during the first blind pass, and recorded here because nothing in the
code carries it yet: **a clip should start at the last bounce or the toss.**
Not the first bounce — a server may bounce the ball five times, and the four
before the last are lead-in, not play.

`pad_start_s` is 0.3 s today, which is a fixed pad off a detector edge and
knows nothing about bounces. Closing that gap is tuning, so it waits on
Gate 0 and on the reading list at the top of this file.

### How that definition cashes out in a verdict

The blind pass judges footage, never edges — a window's edges came from this
file's tiling, which is why audit mode has no boundary keys at all.

| the window shows | verdict |
|---|---|
| serve and rally, filling the window | `clean` |
| real play plus dead time (the tiling cut the serve; the point ends mid-window) | `partly` |
| bouncing before the last bounce, walking, ball retrieval, standing | `not_play` |
| a bounce, then the window ends before the toss, and it cannot be told whether that was the last bounce | `unsure` |

Bouncing is `not_play`, not `unsure`: it is a judgement the reviewer can
actually make, and `unsure` sits in no denominator, so spending it on a
decidable case discards the row.

Boundary flags remain label mode's, on rallies whose edges the detector
actually chose (`derive_boundary_flags`): a clip opening on pre-serve
bouncing is `start_early`, one opening after the toss is `start_late`, one
cutting the point short is `end_early`, one running on is `end_late`. A drag
in timeline mode is better than any of them -- it records the signed
millisecond correction rather than only its direction.
