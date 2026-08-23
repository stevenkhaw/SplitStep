# SplitStep — Camera Viewpoint and Subject-Mode Segmentation

**Date:** 2026-08-20
**Status:** Implemented. **§5, §7 and §9 are SUPERSEDED** — subject mode was validated
on 2026-08-20 and failed. Read
`docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md` before acting on
anything in this document. §4 was also amended during implementation (see §4).
**Extends:** `docs/superpowers/specs/2026-08-19-splitstep-design.md` (§5 detection)
**Supersedes for ground-level sources:** the two-player scoring model in `detect/segment.py`

---

## 1. Problem

Every clip the reviewer produced was one hit long. Median clip 3.9 s against a
median true rally of 8.0 s.

The visible cause was calibration: `MAX_SPEED` was 4.0 body-lengths/sec, taken
from synthetic fixtures that hand every player `v=2.0`. Real measured speeds on
`sessions/2026-08-18/sources/01` are p50=0.11, p95=0.69 — roughly 6x smaller. The
speed term therefore contributed ~0.01 of its possible 0.27, and a plainly
rallying frame scored 0.408 against a 0.45 threshold. Only the ~1 s window around
an audio impact ever cleared it, so each clip collapsed to a single hit.

Fixing the scale exposed the real problem underneath.

### The real problem

The feature stream had an impossible property: every detected person's feet sat
at `y ≈ 0.84 ± 0.02`, near and far alike, while their box heights differed by 4x.
In a perspective image a 4x-smaller person must have feet much higher in frame.

The footage explains it. The phone is propped roughly a foot off the ground
behind the baseline. At that height the entire far half of the court compresses
into a ~2%-tall band at the horizon. The play-region quad
`[[0.02,0.62],[0.98,0.62],[0.98,1.0],[0.02,1.0]]` is a full-width band that
includes the net, the fence, **and every adjacent court behind it**.

| Measured on `sources/01` (5869 frames, 19.5 min) | Value |
|---|---|
| Frames with a near player but no far player | 2226 (38%) |
| Frames with ≥3 people "in region" | 1373 |
| Maximum people in region | 7 |
| Median \|near.foot − far.foot\| | 0.0060 |
| Median `far.h / near.h` | 0.313 |

`split_near_far` takes the largest in-region box as near and the **second largest
as far**. In that horizon band the second largest is whichever stranger on an
adjacent court happens to be biggest. This is the exact failure the design
already warned about: *players on the adjacent court score as your opponent.*

So the 38% "far-player dropout" is not YOLO losing a player. It is how often a
stranger was visible. `both`, `w_outside`, and `speed = min(near.v, far.v)` are
all noise on this source, and no amount of tracking or box persistence fixes
that — it would only make the noise smoother.

## 2. Goals

1. Segment ground-level footage correctly, using the 19.5 min already recorded.
2. Keep working when the camera is mounted higher (fence mount is planned).
3. Choose between the two automatically, with no new human step.
4. Stop calibrating against synthetic signals.

### Non-goals

- Recovering the opponent at ground level. At a foot off the ground the opponent
  and the strangers occupy the same horizon line and are geometrically
  indistinguishable. This design does not try.
- Re-tuning the pair (elevated) model. No validated footage exists for it.

## 3. Architecture

One new pure module, `splitstep/detect/viewpoint.py`:

```python
@dataclass(frozen=True)
class ViewGeometry:
    profile: Literal["pair", "subject"]
    foot_separation: float    # measured median, kept for diagnostics
    subject_min_h: float      # derived per-source box-height floor
    frames_measured: int
    pairs_measured: int       # added by the §4 amendment: the gate counts pairs
    low_confidence: bool

def analyze_view(frames: list[FeatureFrame]) -> ViewGeometry: ...
```

Pure function over the feature stream — no I/O, no DB, no model. It is
recomputed on every segment run rather than stored: it costs ~1 ms, needs no
migration, and the re-segment slider stays instant.

`segment()` keeps its signature and stays pure. The profile reaches it through
`SegmentParams`, so scoring remains params-driven and independently testable.

The three current `SegmentParams(...)` construction sites — `jobs/handlers.py`,
`cli.py`, `api/routes.py` — route through one shared helper instead, following
the `setup.py::queue_setup` precedent so HTTP and CLI cannot drift on which
profile a source gets.

## 4. The classifier

**Discriminator:** median `|near.foot − far.foot|` over frames holding both
boxes. A ground-level camera collapses both players onto the horizon; an
elevated camera separates them by depth.

Measured on the one real source: **0.0060**, p90 = 0.0099, and stable across all
ten chunks of the video (0.0041–0.0077). An elevated camera puts the far player
near the service line, a separation of roughly 0.15–0.35. Any cut in 0.03–0.08
separates cleanly; the spec fixes it at **0.05**, the midpoint of that range —
roughly 5x the observed ground-level p90 and 3x below the expected elevated
minimum. Direction: `foot_separation < 0.05` → `subject`, otherwise `pair`.

**`subject_min_h`** is derived, not a magic constant: `0.5 × median(near.h)` for
the source. On `sources/01` that is 0.113, which sits cleanly between the real
player (0.227 median) and the horizon strangers (0.055 median, 0.099 p90).
Deriving it means it scales with camera height and lens rather than being tuned
to one clip.

**Confidence gate.** *(Amended 2026-08-20 during implementation — see the
amendment note below.)* The classification is a median over **paired** frames,
so the number of pairs is what decides whether it can be trusted. The near-box
count only says whether the source has usable detections at all.

1. No pairs at all → `subject`, `foot_separation = 0.0`. `low_confidence` is
   true only if fewer than 50 frames carried a near box. A far player never
   seen across a long source is the clearest possible subject signal, not a
   weak reading; across a very short source we genuinely do not know.
2. Fewer than 20 pairs (4 s of paired observation at 5 fps) → `subject`,
   `low_confidence=True`. Too few paired samples for a median to mean anything.
3. Otherwise → the median separation decides, `low_confidence=False`.

A low-confidence result is logged as a warning from `params_for_frames`, so it
fires for the detect job, the CLI and the API alike. The warning also covers
the other reading of a sparse stream: a wrong court quad, where almost nothing
is detected and neither profile can help.

No frames at all → `segment()` already returns `[]`. A degenerate
`median(near.h) == 0` floors `subject_min_h` to a small constant so the gate
cannot open on every box.

> **Amendment note.** As first written, this section gated on "fewer than 50
> frames carrying a near box". That measures the wrong quantity: an 8-second
> source with 40 frames, *every one* of them carrying both boxes at an
> unambiguous 0.5 separation, was forced to `subject` with `low_confidence`
> despite the geometry being about as clear as it gets. Forty paired samples is
> ample for a median. The flaw surfaced when wiring the call sites (Task 4) —
> a pre-existing CLI test caught it — and the gate was moved onto the pair
> count rather than bending the test to fit.

## 5. Subject-mode scoring

Presence becomes a **gate**, not an additive term:

```
subject = near is not None and near.h >= subject_min_h

score(t) = 0                                          if not subject
         = (w_speed·near_v + w_lateral·lateral
            + w_hits·hit_rate + w_reg·hit_regularity)
           / (w_speed + w_lateral + w_hits + w_reg)   if subject
```

Two properties drove this shape:

- **Your player is on court 83% of the time**, including between points. As an
  additive term, presence handed out a free 0.303 baseline on nearly every
  frame; as a gate it earns nothing, and audio and motion must carry the frame
  on their own. Measured, the gate fits the data better on all three metrics at
  once (§7).
- **The adjacent-court guard survives and strengthens.** Audio scores exactly 0
  unless a box of your player's size is present. Horizon strangers (h ≈ 0.055
  against a 0.113 floor) can never open the gate, so the existing rule — audio
  alone must never carry a frame — holds without a special case.

Speed uses `near.v` alone rather than `min(near.v, far.v)`. That `min()` was the
term the phantom far player destroyed: a far box re-acquired after a gap reports
`v=0` (`_to_player` sees `prev is None`), and 22% of frames carrying a far box
reported exactly `far.v == 0`.

**Near-player motion is weak on its own.** In-rally vs between-point median
lateral displacement is 0.0048 vs 0.0032 per 200 ms — a separation of only
+0.10 at the best cut, because the player still walks around between points.
Audio carries the discrimination; motion and the gate constrain it.

> **SUPERSEDED 2026-08-20.** The premise of that last sentence is false. Audio
> does not carry the discrimination: impacts fire at 0.56/s across windows a pose
> track confirms are swing-free, against 0.67/s while playing, at every prominence
> floor tested. Stereo localisation and spectral timbre fail too. Subject mode
> therefore has *neither* a strong signal nor a weak one — the weak motion term
> is all that is left. See the validation document.

### Subject-mode defaults

| Parameter | Value |
|---|---|
| `threshold` | 0.25 |
| `close_gap_s` | 2.0 |
| `min_duration_s` | 1.5 (unchanged — the ace floor) |
| `pad_start_s` / `pad_end_s` | 0.3 / 0.5 (unchanged) |

These come from the fit in §7 and inherit its circularity caveat.

### Threshold scale changes

Subject mode's denominator drops the presence weight, so its threshold is **0.25**
against pair mode's 0.45. They are not comparable numbers.

Consequence: the UI can no longer hardcode a default. `ResegmentPanel.svelte` and
`TimelineMode.svelte` must read `threshold` from the `/scores` response, which
already returns it in `ScoreSeries`.

## 6. Pair mode is left alone

Pair mode keeps its current additive shape. Changing two models on evidence from
one source is how this bug happened in the first place.

Its defaults after this work:

| Parameter | Value | Status |
|---|---|---|
| `MAX_SPEED` | 0.7 | Measured. Body-lengths/sec is scale-invariant, so it transfers to both modes. |
| `threshold` | 0.45 | **Unvalidated.** Restored to its original value — a 0.42 fitted during this session was fitted against strangers-as-opponents, which is worse than leaving it alone. |
| `close_gap_s` | 2.0 | Raised from 1.5. Justified by genuine occlusion in elevated footage, not by the phantom dropout. |

The asymmetry — a gate in subject mode, an additive term in pair mode — is a
known wart. Revisit it when elevated footage exists to validate against.

## 7. Fit against the data

Ground truth is audio impacts clustered with a <3 s gap: 566 detected hits,
59 clusters, median 8.0 s, 59% coverage (spans only, no padding).

| Scoring shape | threshold | close_gap | n | median | coverage |
|---|---|---|---|---|---|
| **truth** | | | **59** | **8.0 s** | **59%** |
| gated (chosen) | 0.25 | 2.0 | 61 | 7.6 s | 63% |
| additive presence | 0.45 | 1.5 | 67 | 8.8 s | 68% |
| pair model, as it behaved before this work | 0.45 | 1.5 | 54 | 3.9 s | 22% |

> **SUPERSEDED 2026-08-20.** The circularity flagged below turned out to be the
> smaller problem. The "truth" row is not truth: clustering audio impacts measures
> the *venue's* activity, because the detector fires at the same rate whether or
> not anyone is playing on our court. Every number in this table is a fit against
> that non-signal. **Do not re-fit against audio-impact clusters.** The 0.25
> threshold that came out of it is retained only as a labelled placeholder.

**This fit is partly circular** — audio drives both the score and the labels, so
these numbers cannot be the last word. See §8.

## 8. Testing and validation

- `analyze_view` unit tests: synthetic ground stream (feet equal) → `subject`;
  separated stream → `pair`; sparse stream → `subject` with `low_confidence`.
- Gate tests: audio-heavy weights plus only horizon-sized boxes → no rallies.
  This is the mutation-effective form of the existing adjacent-court guard —
  it fails if the gate is deleted, which the default-weight version does not.
- **A trimmed slice of the real `features.jsonl`, committed as a golden
  fixture.** `tests/fixtures/**/*.jsonl` is already re-included in git for
  exactly this purpose. It is the direct remedy for "tuned on synthetic signals
  only", which is the root cause of this entire spec.
- Every existing pair-mode test must pass unchanged.
- **Non-circular check:** extract ~6 detected intervals as clips and confirm by
  eye that they are rallies with sensible boundaries. Required before the
  subject-mode numbers in §7 are treated as validated.
  **DONE 2026-08-20 — and it FAILED.** Two of six intervals were the operator
  setting the camera down and walking back to stop recording, and confidence is
  inverted (known-false 0.40/0.46 against known-true 0.36/0.39) so no threshold
  separates them. This check did its job: it caught a model built on a phantom
  before it shipped as working. Findings in
  `docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md`.

## 9. Out of scope

- Recovering the opponent at ground level (§2 non-goals).
- Any change to the audio detector. 566 hits at 28.9/min, ~9.6 per rally
  cluster, is a believable structure and is not implicated in this bug.
  > **SUPERSEDED 2026-08-20.** "Believable structure" was pattern-matching, not
  > measurement. The detector *is* implicated: it cannot tell our court from the
  > neighbouring ones, in mono or in stereo, and that is now the single largest
  > obstacle to segmenting ground-level footage. Scoping it out was wrong.
- Storing the classification in the database. Recomputation is cheap and
  avoids a migration; revisit only if a manual override is wanted.
