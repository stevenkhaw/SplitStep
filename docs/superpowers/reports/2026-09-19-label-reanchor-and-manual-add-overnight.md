# Inherited labels and manual rally add — overnight report

**Branch:** `feat/label-reanchor-and-manual-add` (local, nothing pushed)
**Tests:** pytest 1052 · vitest 912 · svelte-check 0 errors · build clean · ruff clean
**Review:** ran over the whole branch diff; 4 confirmed bugs found, all 4 fixed; 3 recorded and left

Baseline at start was pytest 1001 / vitest 833.

## Completed

All seven tasks. Nothing parked.

- **Task 1** — `LabelController` resolves a labelled span by `>= 0.5` overlap when the exact detector-span match is gone, marks it inherited, writes nothing (`a4fcf6c`)
- **Task 2** — label mode renders the inherited badge with a signed drift phrase (`99f6c7c`)
- **Task 3** — `create_rally` in the db layer, null det bounds, overlap allowed (`55134df`)
- **Task 4** — `POST /api/sources/{source_id}/rallies`, 404/400 split mirroring `api_split` (`b879dff`)
- **Task 5** — `scrubMsAt` / `scrubXFor` / `clampSpanToSource` in `lib/timeline.ts` (`7447777`)
- **Task 6** — `N` opens an add in timeline mode, `[`/`]` set bounds, `Enter` commits, `Esc` cancels; new `SourceScrub.svelte` (`3f1e114`)
- **Task 7** — docs: CLAUDE.md's two bullets, and a SMOKE.md section with **zero ticked boxes** (`0f7917c`)
- **Review fixes** — four confirmed bugs (`47e8b81`); cross-language parity pins (`b5ad390`)

## Parked

None.

## What you must check yourself

**Nothing on this branch has been seen in a browser.** Every check was
pytest, vitest or svelte-check. The highest-risk item is the one I could not
test at all:

- **The deck spans to the end of the source while an add is open**, anchored at
  the playhead `N` was pressed on. The reasoning is that `VideoDeck` re-seeks on
  a `startMs` change and will land where the playhead already was, so nothing
  jumps. That is reasoning about a component, not an observation. If `N` makes
  playback leap, this is why.
- Whether the seeded 100 ms draft is findable on the scrub bar — at an hour
  under ~900 px it is well under one pixel and rides entirely on a 3 px floor.
- Whether clicking the bar seeks where it looks like it should, and whether
  `Enter` lands you on the new rally with a sane playhead.
- The inherited badge has only ever rendered in jsdom against fixture rows.
  Whether it reads as "confirm this" rather than "something is wrong" is
  unobserved.

`docs/SMOKE.md` records all of this honestly.

## Judgment calls

The four that would change your mind about the code, in order:

1. **A pre-existing test asserted the opposite of D1 and was rewritten.**
   `does not seed from a label whose span merely overlaps` carried the comment
   *"Matching by overlap here would silently attribute a verdict to a clip
   nobody watched."* That is a real prior position, and D1 reverses it. Kept
   because the objection is to doing it **silently** — the inherited badge is
   what answers it. If you disagree, this is the commit to revisit (`a4fcf6c`).

2. **The empty draft, which I decided (uncovered).** `setInPoint`/`setOutPoint`
   both need a far bound, so `N` seeds a `MIN_RALLY_MS` span at the playhead
   rather than modelling a null draft. Keeps the state machine total and avoids
   an untestable branch in a `.svelte` file.

3. **Bug 1's fix refuses a band pick during an add rather than cancelling the
   draft.** The draft is the only copy of a span picked by eye — the failed-write
   path deliberately preserves it — so discarding it on a stray click would
   contradict that. A structural backstop was added too: the draft now carries
   its own `sourceId`, so the write and the geometry key on one id.

4. **Bug 3's fix compares flags as well as verdict** when deciding whether a
   restored state is still the inherited one. Stricter than the review
   suggested, and right: a flag toggle is its own confirming write.

Covered by decisions: D1 (overlap resolution), D2 (label mode only), D3
(confirming writes against the current span), D6 (`N`, and its intentional reuse
from the queue map), D7 (plain new rally), D8 (overlap allowed — which is
exactly why the `Enter` key-repeat bug mattered), D9 (no auto-cut).

Full list, including the smaller ones, is in the run log beside this file.

## Recorded, not fixed

- **`_renumber`'s idx tie-break on equal `start_ms`.** `ORDER BY s.idx, r.start_ms`
  has no final tie-break, while `split.ts::renumber` relies on `Array.sort`
  stability. Pre-existing — manual drags could already produce it — but D8's
  allowed overlap makes it easier to reach deliberately. `renumber`'s own
  docstring says the two must agree "or the UI prints a rally number the server
  disagrees with."
- **`signedSeconds` would sit better in `lib/time.ts`** as `formatSignedDuration`,
  beside `formatDuration`. Correct where it is; placement only.
- **A source with no rallies at all cannot receive a hand-made one.** Timeline
  mode opens onto a focused rally, so a wholly-undetected source is unreachable.
  The motivating case (pair mode zeroing part of a source that has other
  rallies) is covered; this edge is not.

## Next steps

1. Open the app and walk the two surfaces — the unverified list above is the
   script. `npm run build` first; `serve` mounts `web/dist`.
2. Decide on the three recorded items.
3. `superpowers:finishing-a-development-branch` for the merge.
