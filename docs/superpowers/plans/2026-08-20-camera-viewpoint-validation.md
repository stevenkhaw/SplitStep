# Subject-Mode Validation — Findings

**Date:** 2026-08-20
**Plan task:** `docs/superpowers/plans/2026-08-20-camera-viewpoint.md` Task 5
**Source:** `sessions/2026-08-18/sources/01` (19.5 min, ground-level camera)
**Verdict:** ❌ **Subject mode is not validated. Do not treat spec §7's numbers as real.**
**Corrected 2026-08-21** — clip #1's verdict below was wrong, which weakened one of the
supporting arguments. The verdict itself survives re-testing. See §"Correction" at the end.

---

## What was run

`bootleg segment 0926ad87101b40dc9cd63a915858147c --dry-run` produced **61 rallies at
threshold 0.25**, median 7.6 s — matching the spec §7 prediction exactly. Six intervals
were then inspected frame by frame at their boundaries and interior: the 1st, 10th, 20th,
35th, 50th and last.

## Clip verdicts

| # | Span | Dur | conf | median `near.h` | max `near.h` | frames w/ impact | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | 0:05.1–0:30.7 | 25.6 s | 0.40 | 0.290 | **0.939** | 41% | ⚠️ **MIXED — originally misjudged as pure camera setup.** The first ~8 s is the operator at the lens, but from ~10 s there is real play (racket raised mid-swing at 11.5 s, hitting at 22–24 s). See the Correction. |
| 10 | 3:22.7–3:26.3 | 3.6 s | 0.44 | 0.248 | 0.256 | 39% | ❓ Walking, racket down, no ball visible. Stills cannot settle it. |
| 20 | 6:07.3–6:13.7 | 6.4 s | 0.43 | 0.223 | 0.229 | 50% | ❓ Walking, racket down, no ball visible. Stills cannot settle it. |
| 35 | 10:09.7–10:13.1 | 3.4 s | 0.36 | 0.220 | 0.234 | 76% | ✅ **Serve.** Ball visible above the player at contact. |
| 50 | 15:12.3–15:28.7 | 16.4 s | 0.39 | 0.234 | 0.326 | 55% | ✅ **Rally.** Swings, overhead, ready stance. End boundary may cut ~1 s early. |
| 61 | 19:26.7–19:30.3 | 3.6 s | 0.46 | 0.276 | **0.675** | 72% | ❌ **Teardown.** Player walking back to the camera to stop recording. |

One unambiguous false positive (#61), two confirmed true, one mixed (#1), two unresolvable
from stills.

**Confidence looks inverted, on thin evidence.** The one clean known-false clip scores
0.46 against known-true clips at 0.36 and 0.39, so raising the threshold would remove real
rallies before that false one. This was originally stated on two known-false clips; the
correction below reduces it to one, which is a single data point and should not be leaned
on. The verdict does not rest on it — it rests on the audio measurement, which was re-run
against clean windows and held.

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

> **Caveat, and its resolution.** The "setup 5–31 s" window used here was later shown to
> contain roughly 8 s of real play, so it was about 30% contaminated. The comparison was
> re-run against windows the pose track confirms are swing-free (`above` = 0.00
> throughout): 4–9 s, 14–20 s and 26–31 s pooled give **0.56 impacts/sec** against
> **0.67/sec** across the two confirmed-playing windows. Two of those clean not-playing
> windows individually run at 0.67/s and 0.80/s — *above* the confirmed rally's 0.65/s.
> The conclusion is unchanged and now rests on correctly labelled data.

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

## The stereo lead was tested. It is also a dead end.

`extract_pcm` forces `-ac 1`, and both `original.mov` and `proxy.mp4` carry stereo (AAC,
48 kHz, 2 channels), so directional information was being discarded before anything could
look at it. The channels are genuinely distinct — inter-channel correlation over the rally
window is **-0.297**, not the ~1.0 of a dual-mono file — so the test was worth running.

Impacts in both labelled windows were localised two ways. Plain cross-correlation gave
median |ITD| of 21-23 samples in both windows, which *exceeds the physical maximum* for a
phone's ~10 cm mic spacing (~14 samples at 48 kHz) — it was tracking reverberation, not
direction. Repeating with GCC-PHAT (phase-only, the standard fix for reverberant spaces)
and a lag search bounded to +/-16 samples:

| window | median \|lag\| | at bound | peak sharpness | near-centre (\|lag\|<=3, \|ILD\|<2 dB) |
|---|---|---|---|---|
| setup 5-31 s (no play by us) | 1.0 | 0% | 0.242 | 27% (19 of 71) |
| rally 912-929 s (our play) | 1.0 | 0% | 0.282 | 38% (19 of 50) |

The method works — lags are tight, none pinned at the search bound, peaks sharp enough to
indicate a real direct path. But **both windows centre on a lag of 1 sample**: near and far
impacts alike arrive effectively on-axis. The near-centre fractions differ in the right
direction, 27% against 38%, but that is 19 impacts against 19 on populations of 71 and 50
— roughly 1.3 sigma, not significant.

The phone's mics are too close together to resolve sources at court distances. Stereo does
not rescue the audio path.

## Spectral timbre was tested too. Also dead.

Amplitude and direction having failed, timbre is a third independent axis: air absorbs
high frequencies with distance, so a racket at 3 m and one at 30 m should differ in
brightness even at matched loudness.

A first attempt measured a spectral centroid of ~430 Hz, which is implausible for a racket
strike — the window had no high-pass, so low-frequency ambience dominated and the
measurement described the venue's noise floor rather than the transient. Corrected by
subtracting the spectrum of the 12 ms immediately before each onset, so only the energy
the strike *added* is measured:

| | not playing | rally | serve |
|---|---|---|---|
| excess centroid | 630 Hz | 575 Hz | 594 Hz |
| excess HF/LF | −22.84 dB | −24.22 dB | −22.49 dB |
| loudness-matched HF/LF | −23.24 dB | −22.53 dB | |

No separation, and the bright tail runs the wrong way: 4% of playing impacts clear the
not-playing p90, against 10% by construction. A 3.9 dB hint in the uncorrected version
vanished entirely once measured properly.

The recording is not the limitation — it carries real energy out to 16 kHz, and the proxy
is spectrally identical to `original.mov` within 0.2 dB in every band, so audio work never
needs the 5.5 GB source files. The physics simply does not bite: a few dB of absorption
over 20–30 m of open air, with clear line of sight through a chain-link fence, is not
enough to make the next court sound dull.

**Audio is exhausted.** Three independent axes — amplitude, direction, timbre — all
negative on correctly labelled windows.

## Correction (2026-08-21): clip #1 was misjudged, and pose found the error

Clip #1 was recorded above as "camera setup, no play". That was wrong, and it was wrong in
a way worth recording, because the error was found by the very signal being evaluated.

A pose track (YOLO11-pose at 30 fps, the whole source, aggregated onto the same 200 ms
grid) was extracted to test whether arm movement discriminates. Sampling at 30 fps rather
than the detector's 5 fps matters: a tennis swing lasts ~0.3 s, so at 5 fps it is one or
two aliased samples — which is why an earlier 5 fps attempt showed nothing.

Within clip #1 the wrist-above-shoulder fraction is 0.00 for most of the window but spikes
to 0.20 at 10–12 s and 0.23 at 22–24 s. Frames pulled at those timestamps show the racket
raised mid-swing at 11.5 s and an athletic hitting stance at 22–24 s. The operator places
the camera in the first few seconds, walks out, and starts playing. **The pose feature was
right and the hand label was wrong.**

On the four labelled clips, one pose feature separates cleanly:

| clip | verdict | wrist above shoulder |
|---|---|---|
| #1 camera setup (first 8 s only) | NOT | 6% |
| #61 teardown | NOT | 4% |
| #50 rally | PLAY | 14% |
| #35 serve | PLAY | 16% |

Wrist *speed* and wrist *reach* both overlap — the teardown clip has the highest speed p90
of all four, because walking toward the lens swings the arms fast in normalised terms.
Only the positional feature holds.

Segmenting the whole source on that feature alone (4 s smoothing, threshold 0.10) gives 46
intervals, median 5.9 s, 32% coverage. The teardown false positive disappears entirely,
and the 25.6 s clip #1 collapses to two ~4 s intervals which are both genuine play.

Caveats that keep this from being a validated result: four labelled clips is thin, and one
of them was labelled wrong by the person writing this document. 30 fps pose is roughly six
times the inference cost of the current expensive stage. And the player is tracked in only
81% of bins — a missed detection currently scores identically to "not playing".

## Conclusion

Subject mode as specified has no working discriminator on this footage. Its dominant input,
audio, measures the venue rather than the player — in amplitude, in direction, and in
timbre — and near-player motion separates by only +0.10. That is a capture and feature-set
problem, not a tuning problem, and no threshold fixes it.

Two live routes out, in order of confidence:

1. **Raise the camera.** A fence mount puts the two players at genuinely different depths,
   which is what the pair model assumes and the only configuration with a strong signal
   available. `analyze_view` switches profiles on its own.
2. **Pose features.** Wrist-above-shoulder is the first thing measured in this whole
   exercise that separates play from non-play, and it caught a labelling error its
   evaluator had made. It needs more labelled clips before it justifies the 6x inference
   cost of building it into the pipeline.

Hand-labelling ten to fifteen clips would do more for either route than any further
threshold work. The absence of hand labels is what let audio-impact clustering stand in as
ground truth in the first place.

## 2026-08-21: a real labelled set, and what it says

Fifteen windows were hand-labelled blind — the labelling tool
(`web/public/label.html`, served at `/label.html`) deliberately hid which windows the
detector had flagged and how confident it was, so the judgements could not anchor to the
detector's guesses. Nine were detector intervals sampled across the confidence range; six
were windows the detector ignored entirely, without which only precision can be measured
and never recall. Committed as `tests/fixtures/labels_2026-08-18_source01.json`.

### The detector, measured

| | |
|---|---|
| Precision | **4 of 9** flagged windows contain play (44%) |
| Recall | play missed in **2 of 6** ignored windows |
| Confidence | play mean 0.416 against not-play mean 0.388 |

The single highest-confidence window in the set (0.60) is a false positive: someone walks
directly past the lens. Confidence is not merely weak, it is uninformative — which was
previously argued from two clips and now rests on nine.

### Pose does not separate on real labels

The earlier claim in the Correction section — wrist-above-shoulder separating 4/4 — does
not survive a larger set:

```
play    above: 0.044  0.094  0.188  0.237
none    above: 0.0 x3, 0.003, 0.017, 0.018, 0.026, 0.221, 0.352
```

Two windows drove the overlap and both were investigated. The 0.352 case is the
walk-past-the-lens window, where tracking collapses to 0-2 of 6 frames per bin and
produces an impossible 322 torso-lengths/sec. A tracking-quality filter (>= 4 of 6 frames)
was applied to test whether that explained it. **It did not** — the window still scores
0.400 on five clean bins, because the person walking past genuinely has a raised arm. The
0.221 case is a serve wind-up falling at the very edge of an arbitrary 8 s window.

Reported here rather than filtered away: a principled fix was tried, it failed, and the
negative result stands. Four clips was too small a sample and the earlier claim was
small-sample optimism.

One result does survive. The window scoring highest on pose across the whole set (0.425)
is clip 14 — a serve the detector **missed entirely**. Pose sees play the current model
does not; it also fires on people who are not playing.

### What the set is for

Every idea in this document — audio amplitude, stereo direction, spectral timbre,
box-centre motion, wrist speed, wrist reach, wrist elevation — can now be scored against
one fixed set of human judgements in seconds, instead of against whichever three windows
someone picked by hand. That is the durable outcome of this exercise; the individual
negative results are not.

Fifteen windows remains small. Anything that separates cleanly on it should be re-checked
against a second labelling pass before it is built.

### 2026-08-21: producing that second pass no longer needs a throwaway tool

The fifteen windows above came from `web/public/label.html`, 112 lines with the window
list pasted in as a literal and the results pasted back out through a `<textarea>`. It
could not be pointed at a second source.

Labelling is now a mode in the app — press `L` in the review queue. Verdicts
(`clean` / `not_play` / `partly` / `unsure`) and boundary flags land in the `rally_labels`
table, which anchors to the detector's own span rather than to a rally row and therefore
survives a re-segment. Every manual boundary drag also records a signed millisecond
correction, with no extra keystrokes, so the boundary half of the corpus accumulates
just by reviewing normally.

`bootleg labels score <source_id> --threshold X` scores a candidate segmentation against
whatever has been labelled so far, in well under a second. `bootleg labels export` writes
it out as JSON alongside `labels_2026-08-18_source01.json`.

The caveat above still stands, and the tooling does not soften it: every label attaches to
a span the detector proposed, so this corpus measures precision and boundary error, never
recall over play the detector missed. Six of the fifteen windows here were spans the
detector ignored, and two of them contained play — that measurement still requires
sampling unflagged windows, which nothing in the app does.
