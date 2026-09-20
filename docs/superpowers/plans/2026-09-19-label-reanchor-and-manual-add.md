# Inherited labels, and adding a rally by hand — implementation plan

**Spec:** `docs/superpowers/specs/2026-09-19-label-reanchor-and-manual-add-design.md`
**Branch:** `feat/label-reanchor-and-manual-add`

TDD throughout: write the test, watch it fail for the right reason, then
implement. Gates are the repo's own suites — `pytest -q`, `ruff check splitstep
tests`, `npx vitest run`, `npm run check`, `npm run build`. One commit per task.

`splitstep/detect/` is off-limits for every task in this plan. Nothing here
needs it and a live investigation owns it.

---

## Part 1 — Inherited labels

### Task 1 — Resolve a labelled span by overlap when the exact match is gone

**Files:** `web/src/lib/labels.ts`, `web/tests/labels.test.ts`

`LabelController`'s constructor builds `bySpan` keyed on
`spanKey(source_id, span_start_ms, span_end_ms)` and reads it back with
`spanKey(r.source_id, r.det_start_ms, r.det_end_ms)`. Add a fallback.

1. Port `overlapFraction` into TypeScript beside the lookup, mirroring
   `splitstep/db/rallies.py::overlap_fraction` exactly: overlap divided by the
   **shorter** of the two spans, `0.0` when they do not overlap. Add it to
   `web/src/lib/labels.ts` (or `lib/score.ts`'s neighbourhood if it fits
   better) and unit-test it against the Python behaviour, including the
   `max(1, ...)` guard on a zero-length span.
2. Resolution becomes two steps: exact match first, unchanged. On a miss,
   scan that source's labelled spans and take the highest overlap that clears
   `>= 0.5`. Ties break on the smaller absolute start-time difference, so the
   answer never depends on array order.
3. A record found by the fallback is marked `inherited: true`; an exact match
   is `inherited: false`. Carry it in the controller's output only — write
   nothing, and do not change `LabelRecord` as it comes off the wire.

**Tests** (`web/tests/labels.test.ts`):
- an exact match still wins, and is not marked inherited
- a span shifted by 200 ms resolves by overlap and **is** marked inherited
- a span overlapping under 50% resolves to nothing
- two candidate spans both over 50%: the higher overlap wins, and the result
  does not change when the input array is reversed
- a hand-made rally (`det_start_ms` null) is still filtered out of the
  controller entirely, as it is today

**Commit:** `fix(labels): resolve a judgement whose span moved under a re-segment`

### Task 2 — Show inherited in label mode, and keep it out of the audit pass

**Files:** `web/src/components/LabelMode.svelte`, `web/tests/labels.test.ts` or a
new `web/tests/audit-blindness.test.ts`

1. Label mode marks an inherited verdict as inherited — wording is yours, but
   it must say the span moved, not merely that a verdict exists. Design tokens
   only; no raw Tailwind palette steps, no arbitrary sizes. `font-data` for
   anything that is a count or a timecode.
2. Add a test asserting `web/src/routes/Audit.svelte` does not import or
   construct `LabelController`. A file-content assertion is fine and has
   precedent in `web/tests/tokens.test.ts`. The comment must say why: the blind
   pass is the only measurement that can see recall, and an inherited verdict
   rendered there would bias it.

**Do not** add an inherited marker to the audit route, the session rally list,
or `labels score` output. Label mode only — that was decided.

**Commit:** `feat(labels): say when a verdict was inherited from a span that moved`

---

## Part 2 — Adding a rally by hand

### Task 3 — `create_rally` in the db layer

**Files:** `splitstep/db/rallies.py`, `tests/test_split.py` or a new
`tests/test_rally_create.py`

A function inserting one rally at an arbitrary span with `det_start_ms` and
`det_end_ms` NULL, then renumbering. Reuse whatever `split_rally` already does
for insert-and-renumber rather than growing a second path; read it first.

Validation raises `ValueError` (the API layer turns that into a 400, as
`api_split` does): `end_ms - start_ms` must be at least `MIN_RALLY_MS`, and both
bounds must sit inside the source's duration.

Overlap with an existing rally is **allowed** — do not add a collision check.

**Tests:** the row lands with null det bounds; `idx` renumbering puts it in
time order among its neighbours; a too-short span raises; a span past the
source duration raises; an overlapping span succeeds and both rallies survive.

**Commit:** `feat(rallies): create a rally at an arbitrary span`

### Task 4 — `POST /api/sources/{source_id}/rallies`

**Files:** `splitstep/api/routes.py`, `tests/test_api_rallies_create.py`

Body is `start_ms` and `end_ms`. 404 for an unknown source, 400 for a bad span
— the same 404/400 split `api_split` documents, for the same reason. Returns
the new rally id.

**Tests:** a good span returns 200 and the id; unknown source is 404; too-short
span is 400; the created rally comes back in the session detail with null det
bounds.

**Commit:** `feat(api): a route to add a rally by hand`

### Task 5 — Full-source scrub geometry

**Files:** `web/src/lib/timeline.ts`, `web/tests/timeline.test.ts`

Pure functions only. The component must stay a thin shell — logic in a
`.svelte` file is untestable here, because jsdom has no `<video>`.

What is needed: mapping a pointer x within a bar of width w to a millisecond
within `[0, duration_ms]` and back; clamping a draft span to the source bounds;
enforcing `MIN_RALLY_MS` when in and out cross, mirroring how `setInPoint` and
`setOutPoint` already behave.

Reuse `msToFraction` / `fractionToMs` where they fit rather than adding
near-duplicates.

**Tests:** round-trip a position through both directions; a click past either
end clamps; setting out before in anchors correctly; a zero-width bar does not
divide by zero.

**Commit:** `feat(timeline): scrub geometry for the whole source`

### Task 6 — The add mode in TimelineMode

**Files:** `web/src/components/TimelineMode.svelte`, `web/src/lib/shortcuts.ts`,
`web/src/lib/api.ts`, `web/tests/shortcuts.test.ts`

1. `N` starts an add. `[` and `]` set in and out as they already do, `Enter`
   commits, `Esc` cancels. Add the binding to `shortcuts.ts` — the one place a
   keybinding is written down; the inline strip and the `?` overlay both render
   from it. Confirm `N` is still free in the timeline map before wiring it.
2. A scrub bar over the whole source, driven by Task 5's functions.
3. `api.createRally(sourceId, startMs, endMs)` in `lib/api.ts`, then refresh the
   rally list the way the existing split flow does.
4. The new rally starts plain: no star, no point, no rejection, no note, and
   **no clip job**. Adding a span never starts an encode.

**Tests:** `shortcuts.test.ts` covers the new binding and that nothing collides
within the timeline map. The component itself is verified by hand.

**Commit:** `feat(timeline): add a rally by hand, with a full-source scrub`

### Task 7 — Docs

**Files:** `CLAUDE.md`, `docs/SMOKE.md`

- CLAUDE.md, the `rally_labels` bullet: one or two sentences that the corpus
  survives in the data but the *lookup* did not, and that label mode now
  resolves a moved span by the same `>= 0.5` overlap rule everything else uses,
  read-time only, and never in the audit pass.
- CLAUDE.md, the `det_start_ms IS NULL` bullet: manual add is a second way to
  make one, alongside `C`.
- `docs/SMOKE.md`: rows for both surfaces, honest about what was and was not
  exercised by a human. Nothing here has been seen in a browser during this
  run — say so plainly rather than implying otherwise.

**Commit:** `docs: inherited labels and manual rally add`

---

## Ordering

1 → 2, and 3 → 4 → 6. Task 5 is independent and can land any time before 6.
Task 7 last. If a task parks, everything downstream of it in those chains parks
too.

## Out of scope

- Any change under `splitstep/detect/`
- Re-anchoring label rows during `replace_rallies` — explicitly rejected
- A CLI twin for manual add
- Auto-cutting clips, auto-pruning orphan clips, or touching reel items
- The orphan-clip UI surface discussed but not chosen for this run
