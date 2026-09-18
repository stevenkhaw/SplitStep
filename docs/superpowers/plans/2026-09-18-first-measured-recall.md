# Gate 0 — The First Measured Recall, and What It Says About Pair Mode

**Date:** 2026-09-18
**Source:** `sessions/2026-09-16/sources/01` (fence mount, 50.2 min, 44 candidate
rallies at the pair-profile default threshold 0.45)
**Method:** one blind pass at `#/audit/<source_id>`, seed 0, 20 windows of 8 s,
10 drawn from detector intervals and 10 from the gaps. The reviewer was not
shown which was which. Labelled by Steven.
**Required reading before touching a constant:**
`2026-08-20-camera-viewpoint-validation.md` and its 2026-08-21 correction,
`2026-08-27-gate0-fence-mount-first-look.md`. Nothing here was re-fitted and
nothing here is a licence to.

---

## The numbers

Verdicts over the 20 windows: **13 not_play, 4 clean, 3 partly.**

| | |
|---|---|
| precision | **33%** — 2 of 6 decided windows held play |
| sampled recall, strict (`clean`) | **25%** — 1 of 4 |
| sampled recall, incl. `partly` | **29%** — 2 of 7 |

This is the first recall figure the project has ever had. Every previous one was
`span recall (labelled spans only)`, which is over spans the detector itself
proposed and therefore cannot see a miss.

## Two findings, and only one of them is statistical

### 1. Pair mode scores a hard 0.000 whenever one player is out of frame

**Four of the seven windows holding play score 0.000 across their whole
duration.** Not "below threshold" — zero, at every threshold, by construction.

`_raw_score` in pair mode zeroes `speed`, `lateral`, `hits` and `reg` when
`far is None`, then subtracts `w_outside` (1.2) if any box exists at all:
`-1.2/4.4`, clamped to zero. The reviewer walking out of shot while his partner
serves is enough.

| window | verdict | frames with 2 boxes | max score |
|---|---|---|---|
| 4:30–4:38 | clean | 0% | 0.000 |
| 19:04–19:12 | partly | 2% | 0.000 |
| 29:38–29:46 | clean | 0% | 0.000 |
| 43:59–44:07 | clean | 0% | 0.000 |

This one is not a sample-size claim. It is provable from the code, and these
windows only confirm it happens on real footage at a real rate.

### 2. Confidence is inverted, and now cleanly so

Every false positive scores **higher** than every true positive. The two
distributions do not overlap at all:

```
human said PLAY      0.480  0.516
human said NOT PLAY  0.553  0.569  0.592  0.593  0.640  0.703
```

The consequence is worth stating plainly: **no threshold improves this
detector on this source.** Raise it and the two true positives go first. Lower
it and more false positives arrive while the four 0.000 windows stay invisible.
A threshold sweep is not a lever here.

2026-08-20 reported the same shape and explicitly declined to lean on it — it
rested on one clean known-false clip. It now rests on eight decided windows
with disjoint ranges.

### Why: the detector is finding "two people", not "a rally"

Mean fraction of frames carrying two boxes: **27% in windows holding play, 38%
in windows holding none.** The signal runs backwards. Every false positive is a
stretch where two people are visible and nobody on this court is playing —
consistent with 2026-08-27's finding that the "far player" on a public venue is
usually a stranger, and with `w_both` (weight 1.0, the largest term) being a
presence test rather than a play test.

## What this does not establish

Twenty windows, seven of them holding play, six decided for precision. One
source, one session, one seed. The 2026-08-21 set's own caveat applies with
more force here, not less:

> Fifteen windows remains small. Anything that separates cleanly on it should
> be re-checked against a second labelling pass before it is built.

A single window flips strict recall by 25 points. The inverted-confidence
separation is the finding most likely to soften on a second pass, because it is
the one resting on counts. Finding 1 is not.

The boundary figures in the same output (`start bias -20827 ms`, n=2) come from
older drag rows on this source, not from this pass — audit mode never writes a
corrected span. At n=2 they mean nothing and should not be quoted.

## What to do next, in order

1. **A second pass at another seed** (`Draw another sample`, or
   `labels sample --seed 1`). Cheap, and it is what the caveat above asks for.
2. **Nothing about thresholds.** See above; the lever is not connected.
3. Only then, the two structural questions this raises — whether pair mode
   should degrade rather than hard-zero when one player is undetected, and
   whether a source this mixed (separation p25 0.013, p75 0.182) should be
   classified per-window rather than per-source. Both are model changes, both
   want more labelled windows first, and neither should be attempted against
   audio-impact clusters.

---

## Correction (2026-09-18, second pass): the inverted-confidence claim does not replicate

A second blind pass was run the same evening at seed 1 — 15 of its 20 windows
labelled (7 not_play, 5 partly, 3 clean). It was run precisely because the
section above named inverted confidence as the finding most likely to soften
on a second pass. It softened.

| | seed 0 | seed 1 | pooled |
|---|---|---|---|
| windows labelled | 20 | 15 | 35 |
| precision | 25% (2/8) | 100% (2/2) | **40% (4/10)** |
| recall, strict | 25% (1/4) | 33% (1/3) | **29% (2/7)** |
| recall, incl. `partly` | 29% (2/7) | 25% (2/8) | **27% (4/15)** |
| play windows scoring 0.000 | 4 of 7 | 2 of 8 | **6 of 15** |

### What is withdrawn

> Every false positive scores **higher** than every true positive. The two
> distributions do not overlap at all.

That was true of seed 0 and is **not true pooled.** Seed 1 produced no false
positives at all, and its two true positives score 0.565 and 0.568 — inside
seed 0's not-play range.

```
PLAY      0.480  0.516  0.565  0.568
NOT PLAY  0.553  0.569  0.592  0.593  0.640  0.703
```

Overlapping. The medians still lean the wrong way (play 0.54 against not-play
0.59) and nothing here rehabilitates confidence as a signal — but "cleanly
separated, therefore no threshold can help" was a ten-window claim dressed as a
structural one, and it is withdrawn as stated. The weaker reading that survives
is 2026-08-20's original: confidence is uninformative. That has now failed to
separate on three independent occasions and can be treated as settled.

Precision is the other casualty of small n: 25% against 100% between two passes
of the same source, on denominators of 8 and 2. The pooled 40% is the only one
worth quoting, and it agrees with 2026-08-21's 44% (4 of 9) rather than
contradicting it.

### What replicated, and is now the finding

**Six of the fifteen windows holding play score a hard 0.000 throughout** — 4
of 7 in the first pass, 2 of 8 in the second. Recall sits at 27–29% pooled and
was within four points of that in each pass independently.

This is the one that was never a counting argument. `_raw_score` zeroes every
term and subtracts `w_outside` when `far is None`; the passes only establish
how often that state occurs on real footage, which is roughly **four play
windows in ten.**

### What this changes about what to do next

Nothing in the ordering, but one of the reasons is gone. "No threshold helps"
now rests on the 0.000 windows alone — which is enough, since no threshold
reaches a score of zero — rather than on a separation that turned out to be
noise. Thresholds are still not the lever. The two structural questions at the
end of the previous section are unchanged, and now have 35 labelled windows
behind them instead of 20.

---

## The pairing-rate gate (2026-09-18)

Steven's observation: the trouble is vertical framing — he records in portrait
and walks out of shot. Checked against the library, the intuition is right and
orientation is the wrong way to express it.

| session | # | orientation | profile (before) | foot sep | pair rate |
|---|---|---|---|---|---|
| 2026-08-18 | 1 | landscape | subject | 0.0060 | 59% |
| 2026-08-18 | 2 | landscape | subject | 0.0165 | 90% |
| 2026-08-25 | 1 | landscape | **pair** | 0.1284 | **100%** |
| 2026-08-25 | 2 | landscape | subject | 0.0499 | 99% |
| 2026-08-28 | 1–5 | **portrait** | subject | ~0.000 | 1–12% |
| 2026-09-07 | 1 | landscape | subject | 0.0050 | 26% |
| 2026-09-16 | 1 | **portrait** | **pair** | 0.0951 | **34%** |

All five portrait sources from 08-28 already classified `subject` correctly,
from foot separation alone — orientation would have added nothing there. And
a landscape source loses a player the moment he stands wide enough. Portrait
is the cause; it is not the measurement.

The measurement was already in the feature stream and unused. `analyze_view`
takes the median separation **over paired frames only** and never asks how
rare pairing is, gating only on an absolute floor of 20 pairs — which 2298
clears comfortably. So 2026-09-16's profile was decided on 34% of the frames
carrying anyone, and applied to all of them.

`MIN_PAIR_RATE = 0.5` is a veto, never a vote: it can take `pair` away, never
grant it, so a ground-level camera (which pairs constantly, both players on one
horizon) still classifies subject on separation as before. The denominator is
frames carrying a near box, not every frame — an empty court between points is
not evidence against pairing, and pair mode scores those zero correctly.

**It reclassifies exactly one source in the library: 2026-09-16 source 01.**
Everything else lands where it already was.

### Scored against the 35 labelled windows

| | candidates | precision | recall strict | recall incl. `partly` |
|---|---|---|---|---|
| pair @ 0.45 (before) | 44 | 50% | 29% (2/7) | 27% (4/15) |
| subject @ 0.25 (after) | 98 | 47% | 29% (2/7) | **53% (8/15)** |

Inclusive recall doubles at roughly unchanged precision, for 2.2× the
candidates. By the repo's own stated bias — rejecting a false rally is one
keystroke, a missed rally means rescrubbing an hour — that is the favourable
direction. Strict recall does not move: neither profile is finding the
whole-window rallies it misses.

### What this is not

It is not a validation of subject mode, which 2026-08-20 found has no working
discriminator on ground-level footage; it is a statement that on *this* source
subject beats pair against a human corpus. It is also a threshold chosen to
mean something ("a pair is present more often than not") rather than fitted,
sitting in a gap between two data points — 34% and 100%. A third genuinely
two-player source, or a portrait source that should stay pair, is what would
actually test it.

Nothing was re-detected. The classification runs in `params_for_frames` at
segment time, so a plain re-segment picks it up without touching features or
the GPU — and `rally_labels` survives one, being anchored to detector spans.
