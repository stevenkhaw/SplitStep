# SplitStep — Review UX Fixes

**Date:** 2026-08-20
**Status:** Approved and implemented — `web/src/components/QueueMode.svelte`
and `TimelineMode.svelte`, with the bindings in `web/src/lib/shortcuts.ts`.
**Extends:** `docs/superpowers/specs/2026-08-19-splitstep-design.md` (§6 UI)

---

## 1. Problem

First real review pass on a 61-rally session surfaced five problems, four of them
in the queue's review flow and one that silently destroys data.

The queue was built assuming a decisive reviewer: star or reject, advance
immediately, never look back. Reviewing footage whose detector is known to produce
roughly a third false positives is a different task — you watch a clip more than
once, change your mind, and want to know where you are.

## 2. Changes

### 2.1 `S` and `X` stop advancing

`QueueController.star()` and `.reject()` currently do `this.#index += 1`. Remove
that. `→` (`skip()`) remains the way forward, as it already is.

### 2.2 `X` toggles, like `S` already does

`star()` computes `nowStarred = !previousStarred`, so pressing `S` twice unstars.
`reject()` unconditionally sets `rejected = true`. Make it toggle the same way.

This also narrows a real gap: rejected rallies are filtered out of the queue at
construction (`rallies.filter((r) => !r.rejected)`), so a mis-press is currently
unrecoverable from the UI once the page reloads. Toggling gives un-reject for
free while the rally is still in the queue — i.e. for the rest of the current
pass.

It does **not** fix the reload case: a rally rejected in an earlier session still
never enters the queue, and nothing in the UI can bring it back. Closing that
needs a way to see and clear rejects outside the queue, which is not in this
spec.

### 2.3 `skip()` stops clearing flags

`skip()` returns `rejected: false` regardless of the rally's actual state. Today
that is harmless because `reject()` advances immediately, so a rally is never
skipped straight after being rejected. Once §2.1 lands, pressing `→` after `X`
would silently clear the reject. `skip()` must carry `previousRejected` through
unchanged, exactly as it already does for `starred`.

### 2.4 Only `S`/`X` mark a rally reviewed

`persistAction` currently maps `skip` → `api.reviewed(id)`. With `→` now pressed
on every clip, that would mark the whole session reviewed just for walking
through it.

No backend change is needed: `set_star` and `set_rejected` in
`splitstep/db/rallies.py` already write `reviewed_at = COALESCE(reviewed_at, ?)`,
so `S` and `X` stamp it server-side today. The fix is to make `persistAction`'s
`skip` case a no-op that reports success without calling the API.

`skip` stays a recorded action so `U` can still walk the cursor back; it simply
persists nothing. Because it can no longer fail, its revert path is unreachable —
`revert()` reads `previousStarred`/`previousRejected` and is unaffected.

**Superseded in part, 2026-08-22.** The `reviewed_at` half of this section
stands: `skip` still never marks a rally judged, and only `S`/`X`/`P` stamp
`reviewed_at`. The *session-status* half does not. With one column doing both
jobs there was no way to say "walked through" without also saying "judged", so
2.4 had to refuse both together. Migration 008 split them: `skip` now stamps a
separate `seen_at`, the review queue resumes from it (`QueueController`'s
`firstUnseen`, which is what stopped a skipped-but-unjudged rally being
returned to on every open), and `refresh_session_review_status` reads it — so
skimming a pass end to end does now finish the session, with nothing judged.
That is deliberate: a pass you looked all the way through is a pass you
finished. `POST /api/rallies/{id}/seen` is the endpoint behind it.

### 2.5 A finished clip replays instead of advancing

`onended={() => apply(queue.skip())}` becomes `deck.replay()`. Advancing stays
manual. This matters because the detector's boundaries are unreliable: watching a
clip twice is the normal case, not an exception.

### 2.6 Position counter on the video

`N / total` overlaid top-left of the clip, where `N` is the 1-based cursor
position. The existing header stats (`seen · starred · rejected`) stay; this is
about knowing where you are in the pass without looking away from the video.

### 2.7 Timeline saves report themselves

`TimelineMode.commitBounds` calls `api.setBounds()` on drag release or `[`/`]`,
then swallows failures into `console.error`. Success and failure look identical
from the UI — there is no save button precisely because it saves automatically,
but nothing says so.

Show a brief "saved" confirmation on success, and surface failures through the
existing toast system (`lib/toaster.svelte.ts`, already used by QueueMode but not
by TimelineMode).

## 3. Out of scope

- Any change to the segmentation model or its thresholds.
- The `[`/`]` playhead-outside-rally collapse (see §4) — decided separately.
- Reordering or re-indexing rallies whose bounds now overlap.

## 4. Known related defect, not fixed here

Pressing `[` with the playhead parked past a rally's end collapses that rally to
`MIN_RALLY_MS` (100 ms) at the playhead. `clampMinGap` anchors the in-point the
caller asked for and pulls the out-point to `start + 100 ms`, which correctly
prevents an inverted rally but silently destroys a good one.

Observed on rally 17 of session 2026-08-18: detector bounds 312300–321900 (9.6 s),
stored bounds 328867–328967 (100 ms). It is recoverable — `det_start_ms` and
`det_end_ms` are immutable by design — and it is the only affected rally.

The combination of §2.7 (save feedback) and a timeline undo would have made this
visible. A guard that refuses an in-point past the rally's end, rather than
collapsing, is the real fix.

## 5. Testing

All logic under test lives in `web/src/lib/`, per the project convention that
components are thin shells and jsdom has no `<video>`.

- `queue.test.ts`: `star()`/`reject()` leave the index unchanged; `reject()`
  toggles; `skip()` preserves both flags; `undo()` still restores flags and
  cursor across the new non-advancing actions.
- `persist.test.ts`: a `skip` action calls nothing on the API and reports success;
  `star`/`reject` still call theirs.
- Component tests for the counter and for the replay-on-end binding follow the
  existing `web/tests/` patterns.
- Every existing queue test must pass unmodified, or the change altered behaviour
  the tests were pinning — investigate rather than update the assertion.
