# Rally Split — Deferred Follow-ups

**Date:** 2026-08-23
**Status:** Open. Recorded at merge; none block the feature.
**Feature:** `docs/superpowers/specs/2026-08-23-rally-split-design.md`
**Merged as:** the `rally-split` branch, 15 commits from `8ac1c2c`

The final whole-branch review returned **ready to merge** with no Critical or
Important findings. What follows is everything it raised that was left
undone, plus two corrections to the spec itself. Ordered by what a future
session should do first.

---

## 1. `canSplit`/`canMerge` should return a reason, not a boolean

**Where:** `web/src/lib/split.ts:35`, `:43`; `web/src/components/TimelineMode.svelte:282`, `:312`

`TimelineMode` pushes "The playhead is too close to a boundary to split here."
for *every* `canSplit` failure. But `canSplit` is also false when the playhead
sits far **outside** the rally — which is an ordinary state in timeline mode,
because the ±20s window renders neighbouring rallies and scrubbing seeks
anywhere inside it. Scrub across to look at the next rally, press `C`, and the
app tells you you are too close to a boundary.

The same root cause makes `mergeBack` re-derive its refusal reason by
independently re-testing `rally.det_start_ms !== null`, guessing which
`canMerge` branch fired. That is correct today only because the two branches
are mutually exclusive, and would silently stop matching if their order
changed.

The fix pattern already exists **in this component**: `BoundsEdit`
(`web/src/lib/timeline.ts:111`) is `{ok: true, …} | {ok: false, reason}`, and
`applyEdit` surfaces `reason` directly. Its docstring says it exists precisely
because a refusal that misdescribes itself is a smaller version of the bug
that destroyed rally 17.

Returning `{ok, reason}` from both predicates fixes the wrong message and
deletes the branch-order fragility together, in roughly ten lines.

## 2. Two comments justify greying that does not exist

**Where:** `web/src/lib/split.ts:43`, `web/src/components/TimelineMode.svelte:107`

Both explain their design as "so the key hint can be greyed before the request
rather than after a 400." Nothing greys anything. `KeyHints` renders static
data from `shortcuts.ts`, and spec §7.2 explicitly decided against adding a
disabled-set prop for one key.

The substance is real — both do check before issuing the request — but the
stated purpose is fiction, and in a codebase where comments carry the
rationale a reader will go looking for greying code that was never written.
Reword to say what the check is actually for.

## 3. `api.ts`'s comment annotates the wrong function

**Where:** `web/src/lib/api.ts:118`

The comment explaining why `req` was used instead of `post` sits immediately
above `mergeRally`, which uses `post`. It belongs above `splitRally`.

## 4. Split and merge failures discard the server's sentence

**Where:** `web/src/components/TimelineMode.svelte:296`, `:330`

Both catches push a hardcoded "check that the server is running", throwing
away the actual error. `commitBounds` does the same, so this matches local
precedent — but a 400 from `/bounds` is nearly unreachable (`BoundsBody` only
rejects `end <= start`, which `clampMinGap` already prevents), whereas split
and merge 400 on genuinely reachable states: an earlier failed bounds POST
leaves the local list diverged, and so does a second tab.

`describeApiError(e, 'rally').message` is one line each and would surface
"Cut at 7000ms is not strictly inside rally 1000-5000ms" instead of a guess
about the server being down.

## 5. `mergeBack` never reports success in the status line

**Where:** `web/src/components/TimelineMode.svelte:302`

It sets `saveState = 'error'` on failure but nothing on success, unlike
`splitHere` and `commitBounds`. The toast covers it, so this is an asymmetry
rather than a gap — but the template's own comment calls "✓ saved" the only
thing telling the user an edit took.

## 6. `merge_into_previous` has no rollback test

**Where:** `tests/test_split.py`

`split_rally` has `test_split_leaves_the_set_untouched_when_it_fails`, which
monkeypatches `_renumber` to raise and asserts the rally set is unchanged.
Merge has no equivalent, so its `except: rollback; raise` path is unexercised
— and merge's half-applied state (a *deleted* rally with a stale renumber) is
worse than split's (a duplicated one). The cheapest of these follow-ups.

## 7. Cosmetics

- `web/src/lib/split.ts:82` runs to 126 characters in a file that otherwise
  wraps near 78. There is no prettier config, so nothing enforces it.
- `CLAUDE.md:20` still says `# 680 tests`. HEAD is at **718**. Already drifted
  before this branch; drifted further with it.

---

## Known behaviours, deliberately not changed

**The re-segment panel goes stale while timeline mode is open.**
`ResegmentPanel` reads `detail.rallies` and renders outside the
`{#key rallyRevision}` block, so it stays mounted during a timeline session.
Splits live only in `TimelineMode`'s local list until `closeTimeline`
refetches, so splitting and then re-segmenting *without leaving timeline
first* under-reports the cost and destroys the split. This is pre-existing and
identical for hand-dragged boundaries — the branch inherits it rather than
introducing it. Splits are, however, the first thing lost this way that a
reviewer cannot reconstruct from memory of where a boundary used to be.

**Merging away the rally you opened timeline on loses your queue position.**
`closeTimeline` remounts QueueMode with `startAtRallyId={focusedRallyId}`, and
`jumpTo` no-ops on an id it cannot find. Open timeline on a hand-made half,
press `U`, press `Esc`, and the queue lands on first-unseen — which, since the
half inherited `seen_at`, is usually the end-of-pass state. Narrow, and fixing
it needs the Session callback spec §7.1 deliberately decided against.

**One split reports as two losses.** With no other edits the panel reads
"discard 1 hand-edited boundary and 1 split": the first half's `end_ms` now
diverges from its det span, and the second half is a split. Both statements
are individually true and both losses are real.

**Keys fire behind the `?` overlay.** `KeyHints` intercepts only Escape and the
help key in capture phase, so `C` splits while the modal is up. Pre-existing
and equally true of `[`/`]`.

---

## Two corrections to the spec

**§7.2's premise is false.** It asserts `C` and `U` are "unbound in every mode
today." `U` is *Undo the last verdict* in queue mode
(`web/src/lib/shortcuts.ts:39`) and *Retract this judgement* in label mode
(`:138`). The outcome is nonetheless good — `U` now means "undo my last
action" in all three modes, which is more coherent than a fourth letter would
have been — but the reasoning rested on something untrue, and
`shortcuts.test.ts` pins uniqueness only *within* a mode, so nothing would
have caught it. `C` genuinely is unbound everywhere.

**The corpus quietly reinterprets a split, once the seam is trimmed.** Split
half one at 5000ms, then drag its end back to 4800, and `/bounds` records
`true_end_ms = 4800` against the *parent's* det span `(1000, 9000)` — a 4.2s
"detector ended late" correction whose magnitude is dominated by the split
rather than by any detector error. It is a truthful statement about that
detector interval, and the corpus has no vocabulary for "this proposal
contains two rallies," so it is not wrong. But `splitstep labels score` will
see large boundary corrections appear on any source that has been split, and
neither the spec nor `CLAUDE.md` says so. One sentence in either would save a
future tuning session a confused hour.

---

## Closed at review — recorded so they are not re-raised

- **A shared `_insert_rally_row` helper.** The two INSERTs differ in column set
  *and* placeholder scheme, and each carries a comment explaining its own
  choice. A helper taking every column as a parameter would hide both.
- **A `_require_rally` helper for `api_split`/`api_merge`.** The second lookup
  is not redundant: it is what keeps an unknown id a 404 rather than the 400
  `split_rally`'s `ValueError` would produce, and the routes' docstrings
  explain why that distinction matters to the reviewer's recovery.
- **`renumber`'s `?? 0` rank default.** Unreachable — the only caller derives
  `sourceOrder` from `detail.sources`, a strict superset of the sources any
  rally in that session can name.
- **Two rows tying on both `start_ms` and `end_ms` at merge.** Such rows are
  interchangeable: client and server may extend different ids but produce the
  same *set* of spans, and the refetch on `Esc` erases the difference.
