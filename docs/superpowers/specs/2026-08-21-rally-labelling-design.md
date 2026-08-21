# BootlegVision — Rally Labelling

**Date:** 2026-08-21
**Status:** Approved, ready for implementation
**Extends:** `docs/superpowers/specs/2026-08-20-review-ux-design.md`
**Motivated by:** `docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md`

---

## 1. Problem

The validation exercise ended with one durable artifact and one explicit ask. The
artifact is `tests/fixtures/labels_2026-08-18_source01.json` — fifteen windows
hand-labelled blind, against which audio amplitude, stereo direction, spectral
timbre, box-centre motion and three pose features were each scored and each
killed in a single run. The ask is the document's last line: *"Fifteen windows
remains small. Anything that separates cleanly on it should be re-checked against
a second labelling pass."*

Nothing in the app produces such labels. The tool that produced the fifteen —
`web/public/label.html`, 112 lines with the window list pasted in as a literal and
the results pasted back out through a `<textarea>` — was built for one source on
one afternoon and cannot be pointed at a second.

Meanwhile the review queue records star and reject on every rally, and those are
labels. They are the wrong labels in two ways. They are binary, so they conflate
*"this is not a rally"* with *"this is a rally that starts two seconds late"* —
two failures that live on different knobs. And `replace_rallies` deletes them by
rewriting the rally row on every threshold sweep, carrying only star and reject
across by overlap and discarding manual boundary edits outright.

That last one is the sharper loss. `det_start_ms`/`det_end_ms` sit immutable
beside `start_ms`/`end_ms`, so every boundary drag ever performed has written a
signed detector error in milliseconds — quantitative ground truth, produced as a
byproduct of ordinary review, deleted at the next sweep.

## 2. What this is not

It does not make recall measurable. Every label produced here attaches to a span
the detector proposed, so the corpus is drawn entirely from what the detector
already likes. The validation set was built the other way on purpose: six of its
fifteen windows are spans the detector *ignored*, and two of those six contain
play. That ~33% miss rate is structurally invisible to anything in this spec, and
a metric here must never be named in a way that implies otherwise (§7).

It does not fit weights. Six weights and a threshold cannot be fit on tens of
labels without overfitting, and the labelled set will skew heavily toward `clean`
because the rallies being labelled are ones already reviewed and trimmed.

It does not create signal. The validation document's finding is that on
ground-level footage the *feature set* has no discriminator — audio measures the
venue, near-player motion separates by +0.10, pose fires on people who are not
playing. Labels measure error; they do not supply a feature that separates. The
two live routes out remain raising the camera and pose features, and neither is
unblocked by this work.

What it does: turn every review pass into corpus, and give `segment()` — a pure
function that runs in ~200 ms — a numeric target to be scored against.

## 3. Vocabulary

Two independent groups, because they answer different questions and drive
different parameters. A flat list would force a single choice where both are
often true ("real rally, but it runs on two seconds past the last hit").

**Verdict** — one per span, mutually exclusive. Scores weights and threshold.

| Value | Meaning |
|---|---|
| `clean` | Real rally, boundaries right |
| `not_play` | No play on our court in this span |
| `partly` | Genuinely mixed — the case that broke the first labelling round |
| `unsure` | Stills and audio cannot settle it |

`clean` is an explicit value, not the absence of a tag. A corpus of failures
alone measures nothing: positive controls are half the arithmetic, and
*unlabelled* must stay distinguishable from *labelled good*.

`partly` and `unsure` both exist because of recorded incidents. `partly` is
carried forward from `label.html`, whose own note says binary treatment "is what
produced an earlier mislabelling of a camera-setup clip that actually contained
play". `unsure` exists because two of six clips inspected during validation were
recorded verbatim as "walking, racket down, no ball visible — stills cannot
settle it", and forcing those into `clean` or `not_play` would inject noise into
the corpus while looking like data.

**Boundary** — multi-select. Scores smoothing, padding and minimum gap.

`start_early`, `start_late`, `end_early`, `end_late`

Label mode offers these only under `clean` or `partly` (§6), since a span holding
no rally has no boundary to be wrong about. That is a UI affordance, not a schema
invariant: §5.2 writes flags on rows whose verdict is NULL, and the table permits
it.

`missed_serve` is deliberately absent. It describes a span the detector never
proposed, so there is no row to attach it to; the honest version of that case is
§2's admission that recall is out of scope.

## 4. Data model

New migration `bootleg/db/migrations/003_rally_labels.sql`. 001 and 002 are
applied and are not touched.

```sql
CREATE TABLE rally_labels (
  id             TEXT PRIMARY KEY,
  source_id      TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  span_start_ms  INTEGER NOT NULL,
  span_end_ms    INTEGER NOT NULL,
  verdict        TEXT CHECK (verdict IS NULL OR
                             verdict IN ('clean','not_play','partly','unsure')),
  boundary_flags TEXT NOT NULL DEFAULT '',
  true_start_ms  INTEGER,
  true_end_ms    INTEGER,
  rally_id       TEXT,
  labelled_at    TEXT NOT NULL,
  CHECK (verdict IS NOT NULL OR true_start_ms IS NOT NULL)
);
CREATE INDEX idx_rally_labels_source ON rally_labels(source_id, span_start_ms);
```

`span_start_ms`/`span_end_ms` are the detector span that was judged — always
`det_start_ms`/`det_end_ms`, never the human-edited `start_ms`/`end_ms`. This is
what anchors a label to the source timeline rather than to a row, and it is why a
label stays meaningful after any number of re-segments.

`boundary_flags` is a comma-separated subset of §3's four values in the fixed
order `start_early,start_late,end_early,end_late`, empty string when none. The
canonical order keeps an exported fixture byte-stable across re-exports, the same
reason `features.jsonl` quantizes its floats.

**`rally_id` is plain `TEXT` and deliberately carries no foreign key.** It exists
for provenance only. `replace_rallies` runs `DELETE FROM rallies WHERE source_id
= ?` on every re-segment; a `REFERENCES rallies(id) ON DELETE CASCADE` would
cascade that delete across the entire corpus on the first threshold sweep. The
migration carries this rationale as a comment, because without one the missing
FK reads as an oversight and the next person to touch the schema will "fix" it.

**`verdict` is nullable.** A boundary drag asserts that the edges were wrong and
supplies the right ones; it does not assert a verdict. Defaulting such a row to
`clean` would fabricate a judgement the human never made. The table-level `CHECK`
keeps the resulting freedom bounded: a row must carry a verdict, a corrected
span, or both — never neither.

**The table is append-only.** Re-labelling a span inserts another row; reads take
the latest by `labelled_at` per `(source_id, span_start_ms, span_end_ms)`. This
costs one index and buys a case the project has already hit: clip #1's hand label
was wrong, and the correction — recorded on 2026-08-21, found by the very pose
signal being evaluated — was itself the finding. Overwriting would have erased
it.

**`source_id` cascades.** A source deleted takes its footage with it, and a label
whose video is gone cannot be re-verified. Export before deleting a source.

## 5. Capture

Two write paths. `bootleg/db/labels.py` holds `add_label()` and
`latest_labels()`; the API routes and the CLI both call those, the same
single-source-of-truth arrangement `queue_setup` uses so that HTTP and terminal
cannot drift.

### 5.1 Label mode

`POST /api/rallies/{rally_id}/label` with `{verdict, boundary_flags}`. The server
resolves the rally to its `source_id`, `det_start_ms` and `det_end_ms` and
inserts. The client never sends a span — deriving it server-side is what
guarantees the anchor is always the detector's own guess.

### 5.2 Boundary drag

`POST /api/rallies/{rally_id}/bounds` additionally inserts a boundary-bearing
row: `true_start_ms`/`true_end_ms` from the saved span, `span_*` from the det
span, `verdict` NULL.

Flags are derived from the sign of the delta rather than asked for.
`true_start_ms > det_start_ms` means the detector opened before play began, which
is `start_early`; `true_end_ms < det_end_ms` means it ran on past the end, which
is `end_late`. No row is inserted when the saved span equals the det span —
that is a drag that went nowhere, not a correction.

This path costs no new keystrokes and captures the more valuable half of the
corpus, in milliseconds rather than categories.

### 5.3 Read

`GET /api/sources/{source_id}/labels` returns the latest row per span, so label
mode can render what has already been decided.

**Matching is on exact span, never on `rally_id`.** After a re-segment every
rally id is new. An identical span means the detector produced identical output,
so the previous judgement still applies and the rally shows as labelled; any
different span is genuinely unjudged and shows as unlabelled. Keying the read on
`rally_id` would orphan the whole corpus on the first sweep — the same failure
the missing foreign key in §4 avoids, one layer up.

Note the deliberate asymmetry with §7: reads match exactly, scoring matches by
overlap. Reads answer "have I judged this exact detector output before", where
approximate is wrong. Scoring answers "does this candidate correspond to
something judged", where a new threshold moves every edge and exact is useless.

## 6. UI

A third mode in the Session route beside QueueMode and TimelineMode.
`LabelMode.svelte` is a thin shell over `web/src/lib/labels.ts`, which holds the
cursor, the verdict and flag map, the dirty set and the undo stack — logic in
`lib/`, per CLAUDE.md, or it cannot be tested.

Entered and left with `l` from queue mode.

| Key | Action |
|---|---|
| `1` `2` `3` `4` | `clean` / `not_play` / `partly` / `unsure` |
| `q` `w` | toggle `start_early` / `start_late` |
| `o` `p` | toggle `end_early` / `end_late` |
| `r` | replay span |
| `←` `→` | previous / next rally |
| `u` | undo |
| space | play / pause |

Left-hand keys are the clip's start and right-hand keys its end; the first of
each pair is early and the second late.

Four behaviours differ from queue mode.

**Speed control is gone and playback is pinned to 1x.** `1`/`2`/`3` currently
select 1x/1.5x/2x; label mode takes them for verdicts, matching the muscle memory
`label.html` established. The loss is correct rather than tolerated: at 2x a
reach and a swing are not reliably distinguishable, and a wrong label is worse
than a slow pass. The whole point of the corpus is that it is trustworthy.

**Playback loops within the span.** `label.html` did this deliberately — *"the
boundary is part of what is being judged"*. Letting the video run past the end
means judging the following rally by accident, which matters most for exactly the
`end_late` case the boundary flags exist to capture. Queue mode's run-on is
unchanged.

**Confidence is hidden.** Full blinding is pointless here, since you already know
these spans are detector output. Anchoring is not: the single highest-confidence
window in the existing fixture (0.60) is a false positive, someone walking past
the lens. Seeing the number before judging is a bias with no compensating
benefit, and hiding it costs nothing.

**Nothing auto-advances.** A verdict keypress POSTs immediately and leaves the
cursor in place so boundary flags can be added to the same span; `→` advances.
This matches 95218d3, which removed auto-advance from the queue for the same
reason.

Boundary flags are disabled under `not_play` and `unsure` — there is no boundary
to be wrong about on a span that contains no rally. The header shows
`12 / 47 labelled`.

## 7. Consumption

**Export.** `bootleg labels export <source_id>` writes JSON in a superset of the
existing fixture shape, adding `boundary_flags`, `true_start_ms` and
`true_end_ms`. It writes to stdout, or to `--out PATH`; the intended destination
is beside `labels_2026-08-18_source01.json` in `tests/fixtures/`. No `.gitignore`
change is needed — the ignore list covers `*.jsonl`, and the `!tests/fixtures/**`
re-inclusion exists for golden *feature* fixtures. A `.json` export is not
ignored in the first place.

**Scoring.** `bootleg labels score <source_id> --threshold X` re-runs `segment()`
over cached features and reports against the corpus. Candidates are matched to
labelled spans by **>50% overlap** — the same rule `replace_rallies` uses for
carrying stars, reused rather than reinvented.

| Metric | Computed from |
|---|---|
| precision | candidates matching `clean`/`partly` against those matching `not_play` |
| span recall | labelled `clean` spans with no candidate |
| unknown | candidates matching no label at all |
| start bias / end bias | signed median ms error over rows carrying `true_*` |
| start MAE / end MAE | absolute median ms error over the same rows |

Two constraints on the output, both of which exist to stop a known failure from
recurring.

The recall figure is printed as **"span recall (labelled spans only)"** and is
accompanied by a line stating that it cannot see play the detector never
proposed. The last round's central error was letting audio-impact clustering
stand in for ground truth, which produced a coverage number describing the
venue's activity rather than the player's. A metric named plain "recall" here
would be the same error wearing better clothes.

`unknown` is always printed and never suppressed. A sweep showing 100% precision
across three matched candidates and forty unknowns is not a good sweep, and a
bare percentage conceals precisely that.

`--threshold` sweeps are the intended loop: `segment()` is pure and runs in
~200 ms, so scoring a range of thresholds against fixed human judgement is a
sub-second operation with no GPU involved. That is the same property the
two-stage detector split was built for.

## 8. Errors

- A label POST against a rally deleted by a re-segment in another tab returns
  404; the client toasts and re-keys, which the queue already does on detail swap
  (`web/tests/requeue-on-detail-swap.test.ts`).
- A `CHECK` violation surfaces as 400, not 500.
- `bootleg labels score` on a source with no `features.jsonl` returns the same
  409 the re-segment route already returns for that condition.

## 9. Tests

**Python.**

- `tests/test_labels.py` — insert and latest-per-span ordering; re-labelling
  appends and preserves history; both `CHECK` constraints reject their bad rows.
- **The load-bearing case: a label corpus survives `replace_rallies`.** Approach
  B exists entirely for this property, and the test fails loudly if a foreign key
  is ever added back to `rally_id`.
- The bounds route inserts a boundary row with correctly signed derived flags,
  and inserts nothing when the saved span equals the det span.
- `tests/test_label_score.py` — scorer arithmetic against synthetic labels.
  Synthetic input is appropriate here and does not violate CLAUDE.md's rule: that
  rule bans *calibrating tuning constants* against synthetic fixtures, and this
  file fits no constant. It checks that overlap matching, medians and the
  unknown count are computed correctly.

**Frontend.**

- `web/tests/labels.test.ts` — state machine transitions, flag toggling, flags
  disabled under `not_play`/`unsure`, undo, and exact-span matching against
  labels already stored.

`pytest` runs with `filterwarnings = ["error"]`; ruff line length is 100.

## 10. Out of scope

- Sampling spans the detector did not flag, which is what real recall requires.
- Any re-fit of `segment()` weights or thresholds.
- Cross-source or cross-session corpus views.
- Deleting or editing labels through the UI; the table is append-only and
  `bootleg labels export` is the only read path outside the app.
