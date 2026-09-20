# Inherited labels and manual rally add — overnight run log

**Started:** 2026-09-19
**Branch:** feat/label-reanchor-and-manual-add
**Plan:** docs/superpowers/plans/2026-09-19-label-reanchor-and-manual-add.md
**Decisions:** docs/superpowers/plans/2026-09-19-label-reanchor-and-manual-add-decisions.md

Baseline at start: pytest 1001 passed, vitest 833 passed, svelte-check 0 errors,
ruff clean, tree clean, on the feature branch.

Every judgment call is appended here as it happens, with which decision or
policy covered it — or "uncovered, judgment".

---

## Task 1 — overlap resolution for a moved span (`a4fcf6c`)

Done. vitest 846 passed (from 833), svelte-check clean.

**Judgment call — a pre-existing test asserted the opposite and was rewritten.**
`web/tests/labels.test.ts` carried `does not seed from a label whose span merely
overlaps`, whose comment read: *"A re-segment that moved this edge produced
different detector output, so the old judgement is not a judgement of this span.
Matching by overlap here would silently attribute a verdict to a clip nobody
watched."*

That is a real prior position, not an oversight, and D1 reverses it deliberately.
Kept because the objection is specifically to doing it **silently** — the
inherited marker required by D2 is what answers it. The reviewer is told the
span moved rather than shown a second-hand verdict as first-hand.
Covered by D1 + D2, but it is the most consequential change in this run and
belongs in the morning report.

**Uncovered, judgment — three small extensions beyond the literal task text:**
- `HistoryEntry` gained an `inherited` field so an undo restores the badge
  instead of promoting an inherited verdict to first-hand. Follows from D3;
  the plan did not spell it out.
- The exact-match branch wins even for a verdict-less (boundary-drag) row.
  Falling through would reach past a row written against this very span.
- The Audit blindness guard test, which the plan assigned to Task 2, landed
  here. Task 2 must not duplicate it.

## Task 2 — inherited badge in label mode (`99f6c7c`)

Done. vitest 856 passed, svelte-check clean, build clean. Verified: `Audit.svelte`
and `splitstep/detect/` untouched across both commits; no raw palette steps or
arbitrary sizes in the new markup.

**Uncovered, judgment — `inheritedDriftPhrase` does not reuse `formatDuration`.**
`lib/time`'s formatter clamps with `Math.max(0, ms)`, so any edge where the
judged span sat *earlier* than the current one would print `0.0s` — and a start
edge moving earlier is the common direction under a re-segment. A private signed
formatter was added instead. Correct, but it is a second duration formatter in
the codebase; worth a look in review.

**Uncovered, judgment — the drift phrase is logic, so it lives in `lib/`.**
Follows the house rule that a `.svelte` file cannot hold testable logic. The
component is a thin `{#if}` over one `$derived`.

Note carried forward: the badge has only ever rendered in jsdom. Task 7 must say
so in `docs/SMOKE.md` rather than implying a human saw it.

## Task 3 — `create_rally` in the db layer (`55134df`)

Done. pytest 1015 passed (from 1001), ruff clean.

**Uncovered, judgment — a new mirrored constant, `MIN_RALLY_MS = 100` in Python.**
The floor only existed client-side (`web/src/lib/timeline.ts`). The subagent
mirrored it the way `NOTE_MAX_CHARS` is mirrored in `web/src/lib/notes.ts`,
which is real precedent. But note the drift risk: unlike `score.py`/`score.ts`,
which `tests/fixtures/score_cases.json` pins together, and unlike
`NOTE_MAX_CHARS`, which is enforced at the API boundary too, **nothing tests
that these two 100s agree**. Flagging for the review pass rather than fixing —
adding a cross-language pin is arguably scope beyond the plan.

**Uncovered, judgment — the floor is enforced in `create_rally` but deliberately
not in `split_rally` or `/bounds`.** The subagent's reasoning: a split cuts a
span the detector already proposed, a hand-drawn span has no provenance. That
is a real asymmetry and it is commented in the source. Reasonable, and it
avoids changing existing behaviour, which would have been scope creep.

**Judgment — followed `split_rally`'s shape rather than calling it.** The plan
said "reuse whatever `split_rally` already does". `split_rally` is inherently
parented (reads a row, copies its flags, shortens the original), so there was
nothing to call. Both now share one insert idiom: negative placeholder idx →
`_renumber` → single try/rollback/commit. This satisfies the plan's intent (no
second insert path) even though it is not literal reuse.

**Judgment — `confidence 0.0` on a hand-made rally.** The column is NOT NULL.
Commented so nothing reads it as a detector score.

## Task 4 — `POST /api/sources/{source_id}/rallies` (`b879dff`)

Done. pytest 1020 passed, ruff clean. 404 (unknown source, pre-checked with
`get_source`) kept distinct from 400 (bad span, from `create_rally`'s
`ValueError`), matching `api_split`'s documented split.

**Uncovered, judgment — response key is `rally_id`, not `new_rally_id`.**
`api_split` returns `new_rally_id`, where `new_` distinguishes the created rally
from the one named in the path. This path names a source, so there is nothing to
distinguish from. The plan never fixed the name. Task 6 was told to read
`rally_id`.

**Uncovered, judgment — `CreateRallyBody` carries no pydantic validator.**
Every rule about the span needs the source's `duration_ms`, which pydantic
cannot see. A partial validator would answer 422 for the half it could check and
400 for the rest — one incoherent span arriving as two statuses depending on how
it was incoherent. Reasoning is commented on the model.

## Task 5 — full-source scrub geometry (`7447777`)

Done. vitest 869 passed, svelte-check clean. Added `scrubMsAt`, `scrubXFor`,
`clampSpanToSource`; reused `fractionToMs`/`msToFraction` for clamping.

**Good refusal to duplicate.** The subagent declined to add a `setDraftIn`/
`setDraftOut` pair, because `setInPoint`/`setOutPoint` already enforce
"in and out cannot cross" by refusing with a reason rather than collapsing —
the behaviour that exists because of the rally-17 destruction. A second pair
would have been a second name for one rule, free to drift. Task 6 composes the
existing setters with `clampSpanToSource` instead.

**Uncovered, judgment — the empty draft, decided by me.** The subagent flagged
rather than guessed: `setInPoint`/`setOutPoint` both require a far bound, so
what does `N` produce before either bracket is pressed? Decision: **`N` seeds a
`MIN_RALLY_MS` span at the current playhead**, so the setters always have a far
bound and there is no null case to model. Rationale: it is the least surprising
reading of "start an add here", it keeps the state machine total, and it avoids
a null-draft branch in a component that cannot be tested. Inside the
trivial/reversible line in the decisions doc (local structure); the seeding
helper goes in `lib/`, not the `.svelte` file.

## Task 6 — add mode in TimelineMode (`3f1e114`)

Done. vitest 889 passed, svelte-check clean, build clean. `splitstep/detect/`
untouched across the whole run, verified by diff against the base commit.

**Judgment — Task 6 added `setDraftIn`/`setDraftOut`, which Task 5 declined to
add. Accepted.** Task 5's objection was to a second *implementation* of the
crossing rule, free to drift from `setInPoint`/`setOutPoint`. What landed is a
three-line composition that calls `setInPoint` and returns early on refusal, so
the rule still lives in exactly one place. The alternative — composing in the
`.svelte` file — would have put branching logic somewhere jsdom cannot test it,
against the house rule. This is the better of the two.

**Uncovered, judgment — the deck spans to the end of the source during an add.**
The out-point is what pauses playback, and watching un-proposed footage is the
entire point of the feature, so a deck bounded by the current rally would make
the mode useless. The in-point becomes the playhead at the moment `N` was
pressed, so VideoDeck's re-seek-on-`startMs`-change lands where the playhead
already was. **Reasoned, not observed** — this is the single most likely thing
in the run to be wrong on screen.

**Uncovered, judgment — `Esc` keeps one shortcut entry**, relabelled "Cancel the
add, or back to the queue". A second `Esc` entry would trip the once-per-mode
uniqueness check in `shortcuts.test.ts`.

**Uncovered, judgment — `applyAdd` lives in `lib/split.ts`** to reuse its private
`renumber`. A third copy of that ordering would be worse than the coupling.

**Caught by an existing test, worth noting:** inserting the Add group shifted
`TIMELINE[3]` to `TIMELINE[4]` and the `PRIMARY` strip test failed immediately.
The pinned-six strip held.

## Task 7 — docs (`0f7917c`)

Done. Docs only. SMOKE.md section has **zero ticked boxes** — nothing on this
branch has been seen in a browser.

## Verification pass

pytest 1020, vitest 889, svelte-check 0 errors, build clean, ruff clean.

Code review over the whole branch diff returned **audit blindness: PASSED**
(structurally — `Audit.svelte` seeds through `AuditController`, which keys on an
exact span AND gates on `inSample`), clean design tokens, and four CONFIRMED
bugs, all in code this run wrote:

1. An open add survives a source change. `OverviewBand`'s `onpick` is ungated
   while a draft exists, so `N` on source A → click a source-B rally → `Enter`
   commits A's span against B. Same class as the route-effect staleness bug
   CLAUDE.md pins.
2. `Enter` has no in-flight guard. `commitAdd` nulls `draft` only after the
   await, so key repeat fires N identical POSTs — and D8 removed the server-side
   collision check deliberately, so every one succeeds.
3. `LabelController.restore` drops the inherited mark. A failed write puts the
   verdict back without the badge, so a second-hand judgement reads as
   first-hand while the corpus holds nothing — the exact failure this feature
   exists to prevent. `undo` handles it correctly; `restore` was missed.
4. `bestOverlapping`'s ranking is not the total order its own comment promises.
   Two corpus rows with the same `span_start_ms` and different `span_end_ms`
   tie on all three clauses, so input order picks between contradictory
   verdicts.

All four are bugs in new code, small and reversible — fixing through the
subagent loop rather than recording and leaving.

Also fixing (cheap, and this run created the exposure): a file-content pin for
`MIN_RALLY_MS` across the two languages, a shared fixture pinning
`overlapFraction` to `overlap_fraction` in the `score_cases.json` pattern, the
audit guard widened past one class name, and three inaccurate comments.

Recording but NOT fixing: `signedSeconds` would sit better in `lib/time.ts` as
`formatSignedDuration` (correct where it is, placement only); the `_renumber`
idx tie-break on equal `start_ms`, which is pre-existing and which allowed
overlap makes easier to hit; and that a source with no rallies at all cannot be
reached by timeline mode, so the feature cannot add a rally there.

## Review fixes

`47e8b81` — all four confirmed bugs, each with a test that failed first.
Bug 1 got both a gate and a structural backstop: the subagent chose refuse-over-
cancel for the band pick (the draft is the only copy of a span picked by eye, so
discarding it on a stray click contradicts the failure path that deliberately
keeps it), AND moved `sourceId`/`anchorMs` into the draft so the write and the
geometry key on one id. Bug 3's fix compares flags as well as verdict, which is
stricter than suggested and correct — a flag toggle is its own confirming write.
Bug 4 became a lexicographic ranking key, so "total order" is expressed rather
than asserted in a comment.

`b5ad390` — parity pins. **The two overlap implementations agreed on all 15
fixture cases exactly**, under strict equality; no behaviour was changed on
either side. `tests/fixtures/overlap_cases.json` is now read by both suites in
the `score_cases.json` pattern, `MIN_RALLY_MS` has a file-content guard, and the
audit-blindness guard covers three symbols across two files instead of one
symbol in one file.

Judgment inside that fix: the widened audit guard strips comments before
matching, because `audit.ts` names `LabelController.restore` in a doc comment.
A guard satisfiable by deleting an explanation would be the wrong incentive
here; what must not appear is a *use*. Agreed.

## Final state

pytest 1052 · vitest 912 · svelte-check 0 errors · build clean · ruff clean.
10 commits. Nothing pushed. No task parked.
