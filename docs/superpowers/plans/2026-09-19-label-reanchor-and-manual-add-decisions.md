# Inherited labels and manual rally add — overnight decisions

**Plan:** docs/superpowers/plans/2026-09-19-label-reanchor-and-manual-add.md
**Spec:** docs/superpowers/specs/2026-09-19-label-reanchor-and-manual-add-design.md

## Standing policies

- **Packages:** no new dependencies. A task that seems to need one parks.
- **Retry budget:** 2 debug attempts on a failing task, then park.
- **Off-limits:** `splitstep/detect/` entirely — a live investigation owns it
  and its tuning constants are frozen. Also: no migrations (nothing here needs
  one), and no changes to `reel_items` or clip files.
- **Commits:** one per task, as the plan specifies.
- **Trivial/big line:** wording, class names within the token system, test
  names, local structure, and which existing helper to reuse are mine to
  decide. Anything that changes the schema, adds a dependency, alters a route's
  contract beyond what the plan states, touches the audit route's blindness, or
  widens scope beyond the plan parks.

## Decisions

### D1: How should a judgement survive its span moving?
**Answer:** Resolve by overlap at read time, in `LabelController`. Reuse the
existing `>= 0.5` rule from `overlap_fraction` — the same one `replace_rallies`
and `labels score` use. Write nothing during `replace_rallies`; re-anchoring
rows was considered and rejected because it asserts the reviewer judged a span
they never saw.
**Applies to:** Task 1

### D2: Where may an inherited verdict be shown?
**Answer:** Label mode only. Not the audit route, not the session rally list,
not `labels score` output. The audit pass is blind by design and an inherited
verdict there would bias the only measurement that can see recall.
**Applies to:** Task 1, Task 2

### D3: What happens when the reviewer confirms an inherited verdict?
**Answer:** An ordinary label write against the rally's current det span. The
judgement stops being inherited and the next re-segment starts from a clean
exact match. No special case in `LabelWriter`. An inherited verdict that is
never confirmed is never written.
**Applies to:** Task 2

### D4: Where does manual clip-add live?
**Answer:** A mode inside TimelineMode, not a new route. Timeline already owns
boundary editing, the overview band, `VideoDeck` and the split key.
**Applies to:** Task 6

### D5: How is the span picked?
**Answer:** A full-source scrub bar plus the existing `[` and `]` in/out keys.
No new in/out vocabulary — reusing those keys is the reason the mode belongs in
timeline. `Enter` commits, `Esc` cancels.
**Applies to:** Task 5, Task 6

### D6: Which key starts an add?
**Answer:** `N`. Free in the timeline map (`[ ] C U Space , . Esc ?`). It must
be registered in `shortcuts.ts`, the single place a keybinding is written down.

Note that `N` already means "Write a note" in the **queue** map. That is not a
collision — modes carry separate maps, and the same reuse already exists for
`U` (merge in timeline, undo in queue) and `C`. Do not rename either binding or
park over this.
**Applies to:** Task 6

### D7: What state does a manually added rally start in?
**Answer:** `det_start_ms`/`det_end_ms` NULL, no star, no point, no rejection,
no note. The absence of det bounds is the human-made marker — no new boolean.
**Applies to:** Task 3, Task 6

### D8: What if the new span overlaps an existing rally?
**Answer:** Allow it. No collision check. The rally list is not a partition,
`clip_relpath` is span-derived so two overlapping rallies name two different
files, and score replay orders by `idx`.
**Applies to:** Task 3

### D9: Does adding a span cut the clip?
**Answer:** No, never. Consistent with "never auto-enqueues the cuts". Cutting
stays the existing explicit button.
**Applies to:** Task 6

### D10: Is there a CLI twin for manual add?
**Answer:** No. Out of scope. Unlike setup and scoring, where HTTP and terminal
both validated and drift was the risk, picking a span by eye is not expressible
in a terminal.
**Applies to:** Task 4

## Delegated

Nothing was delegated — every question was answered directly.

## Not in this run

Discussed and deliberately excluded: auto-pruning orphan clips on re-segment
(rejected — makes the destructive reading the default on a 200 ms operation),
auto-clearing orphaned reel items (rejected — reintroduces what migration 007
exists to prevent), auto re-cutting clips, and a UI surface for the existing
`clips orphans` / `clips prune` sweep (a real gap, but not chosen for tonight).
