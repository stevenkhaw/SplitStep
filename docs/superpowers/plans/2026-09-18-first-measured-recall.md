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
