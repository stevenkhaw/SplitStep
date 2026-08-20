# Subject-Mode Validation — Findings

**Date:** 2026-08-20
**Plan task:** `docs/superpowers/plans/2026-08-20-camera-viewpoint.md` Task 5
**Source:** `sessions/2026-08-18/sources/01` (19.5 min, ground-level camera)
**Verdict:** ❌ **Subject mode is not validated. Do not treat spec §7's numbers as real.**

---

## What was run

`bootleg segment 0926ad87101b40dc9cd63a915858147c --dry-run` produced **61 rallies at
threshold 0.25**, median 7.6 s — matching the spec §7 prediction exactly. Six intervals
were then inspected frame by frame at their boundaries and interior: the 1st, 10th, 20th,
35th, 50th and last.

## Clip verdicts

| # | Span | Dur | conf | median `near.h` | max `near.h` | frames w/ impact | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | 0:05.1–0:30.7 | 25.6 s | 0.40 | 0.290 | **0.939** | 41% | ❌ **Camera setup.** Person crouching over the lens, then walking to position. No play. |
| 10 | 3:22.7–3:26.3 | 3.6 s | 0.44 | 0.248 | 0.256 | 39% | ❓ Walking, racket down, no ball visible. Stills cannot settle it. |
| 20 | 6:07.3–6:13.7 | 6.4 s | 0.43 | 0.223 | 0.229 | 50% | ❓ Walking, racket down, no ball visible. Stills cannot settle it. |
| 35 | 10:09.7–10:13.1 | 3.4 s | 0.36 | 0.220 | 0.234 | 76% | ✅ **Serve.** Ball visible above the player at contact. |
| 50 | 15:12.3–15:28.7 | 16.4 s | 0.39 | 0.234 | 0.326 | 55% | ✅ **Rally.** Swings, overhead, ready stance. End boundary may cut ~1 s early. |
| 61 | 19:26.7–19:30.3 | 3.6 s | 0.46 | 0.276 | **0.675** | 72% | ❌ **Teardown.** Player walking back to the camera to stop recording. |

Two unambiguous false positives, two confirmed true, two unresolvable from stills.

**Confidence is inverted.** The two known-false clips score 0.40 and 0.46; the two
known-true score 0.36 and 0.39. Raising the threshold removes true rallies *before* it
removes the false ones, so no threshold setting fixes this.

## Why: neither discriminating signal actually discriminates

### 1. Audio impacts fire at the same rate whether or not anyone is playing

Impact counts in a window with **no play on our court** (camera setup, 5–31 s) against a
**confirmed rally** (912–929 s):

| `prom_frac` | total hits | setup 5–31 s | rally 912–929 s |
|---|---|---|---|
| 0.30 (default) | 566 | 16 (**0.62/s**) | 11 (**0.65/s**) |
| 0.45 | 332 | 11 (0.42/s) | 6 (0.35/s) |
| 0.60 | 209 | 6 (0.23/s) | 4 (0.24/s) |
| 0.75 | 144 | 5 (0.19/s) | 3 (0.18/s) |

The rate is the same to within noise, at every prominence floor. Raising the floor culls
both equally. **The detector is measuring a busy multi-court venue, not this player.**

This invalidates the ground truth the whole subject-mode fit was built on: the "59 audio
clusters, median 8.0 s, 59% coverage" in spec §7 describes *the venue's activity*, not
this source's rallies. Spec §9's claim that audio "is not implicated in this bug" is
wrong.

### 2. Near-player motion barely separates

Already measured before implementation and recorded in spec §5: in-rally vs between-point
median lateral displacement is 0.0048 vs 0.0032 per 200 ms — a separation of +0.10 at the
best cut. The player walks, retrieves balls and repositions between points, which looks
the same as playing.

Subject mode has exactly two discriminating inputs. One is contaminated and the other is
weak, so the model cannot separate play from non-play on this footage at any threshold.

### 3. An upper bound on the gate does not rescue it

The two clear false positives share a signature — a box far larger than playing size
(0.939 and 0.675 against a 0.227 median), i.e. a person at the lens. 11 of 61 intervals
contain such a box. Adding `near.h <= 2 × median(near.h)` to the gate was tested:

| upper bound | n | median | coverage | setup clip gone? | teardown clip gone? |
|---|---|---|---|---|---|
| none | 61 | 7.6 s | 63% | no | no |
| 2.5× median | 61 | 7.4 s | 62% | no | no |
| 2.0× median | 61 | 7.4 s | 61% | no | no |
| 1.6× median | 62 | 7.4 s | 59% | no | no |

It does not work, because only a few frames of the setup interval hold the giant box. The
rest is the player at normal size walking around while the venue's ambient impacts score
the frame. The problem is the feature set, not the gate's shape.

## What survives this finding

- **Task 1 fixture** — real data, still the right thing to calibrate against.
- **Task 2 classifier** — sound and independently verified. Foot separation genuinely
  measures camera height (0.006 here, stable across all ten chunks of the video) and is
  unaffected by any of the above.
- **Tasks 3–4 machinery** — `params_for_frames`, the profile plumbing, the gate mechanism
  and the single-source-of-truth wiring are all fine. It is the subject-mode *weights and
  threshold* that are unsupported.
- **`MAX_SPEED = 0.7`** — a units fix measured off the real player. Unaffected.

## What does not survive

- Spec §5's premise that "audio carries the discrimination".
- Spec §7's fit table, and the 0.25 threshold derived from it.
- Spec §9's exclusion of the audio detector from scope.

## Unexploited signal found while investigating

`extract_pcm` forces `-ac 1`. Both `original.mov` and `proxy.mp4` carry **stereo** audio
(AAC, 48 kHz, 2 channels). Our court's impacts are close and roughly on-axis; neighbouring
courts are off to the sides. A phone's mic spacing is small, so the separation may be
weak — but the channel information is currently being discarded before anything can look
at it. Worth testing before concluding the audio path is a dead end.
