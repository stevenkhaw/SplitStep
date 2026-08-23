# SplitStep — session handoff

Copy everything below into a new chat.

---

I'm working on **SplitStep**, a local tennis video tool I built at `/Users/stevenkhaw/Documents/GitHub/SplitStep`. It ingests phone footage of my tennis sessions, automatically cuts it into rallies, and lets me review them in a keyboard-driven pass — star the good ones, reject false positives, fix boundaries — then eventually compile starred rallies into highlight reels.

**The code is complete. One real session has now been through it, and validating the
detector against that footage produced a negative result** — read "Known weakness" below
before you touch any tuning constant. The short version: at my camera height the detector
cannot tell play from not-play, and the reason is not the weights.

## Setup

```bash
conda activate splitstep
cd /Users/stevenkhaw/Documents/GitHub/SplitStep
```

Library lives on an external SSD: `/Volumes/SanDisk_2TB/SplitStep` (1.8 TB free).

I have this alias:
```bash
alias bv="conda run -n splitstep splitstep --library /Volumes/SanDisk_2TB/SplitStep"
```

Machine: M2 MacBook Air, 8 GB RAM. ffmpeg 9.0.1. torch 2.13 with MPS. `detect_accel()` returns `videotoolbox` / `h264_videotoolbox` / `mps`, so both transcode and inference are GPU-accelerated.

## How it works

- Drop a video in `<library>/_inbox/`. A watcher picks it up once the file size stops changing, moves it to `sessions/<date>/sources/NN/`, transcodes a 1080p H.264 proxy, then runs detection.
- **Detection is two-stage on purpose.** An expensive stage (YOLO11 person detection at 5 fps + audio impact detection) writes `features.jsonl`. A cheap pure function (`segment()`, ~200 ms) turns those features into rally intervals. That split exists so I can retune a threshold without re-running the GPU work.
- Review UI: `bv serve`, then http://127.0.0.1:8420. Queue mode autoplays rallies; `S` stars, `X` rejects, `U` undoes, `←`/`→` navigate, `1`/`2`/`3` set speed, `T` opens a timeline for fixing boundaries.

## The actual task

The weights were originally tuned on synthetic signals only. One real 19.5-minute session
has since been ingested and the detector validated against it on 2026-08-20. Two of the
three original unknowns now have answers, and they are not the answers I wanted:

1. **Do the thresholds find actual rallies at my camera angle** (phone ~1 ft off the
   ground, behind the baseline, public courts with adjacent games in frame)? **No.** At
   that height the far half of the court collapses onto the horizon, so the "far player"
   the detector scored was whoever was standing on an adjacent court. The replacement
   `subject` profile then failed its own validation: camera setup and teardown score as
   rallies, and confidence is *inverted*, so no threshold separates true from false.
2. **Does audio impact detection survive the environment?** The problem is not wind, it is
   neighbours. Impacts fire at 0.56/sec across windows verified swing-free, against
   0.67/sec while actually playing — the detector measures the venue. Stereo direction
   and spectral timbre fail too: the mics are too close together, and a few dB of air
   absorption over an open court is not enough to dull the next court's hits.
3. **Does a 4K-derived proxy play and scrub acceptably in the browser?** Still open.

**So the useful task now is not tuning.** It is either (a) shooting a fence-mounted
session so the two-player `pair` model has a real signal to work with — `analyze_view`
will switch profiles automatically — or (b) finding a discriminator that actually works at
ground level (ball detection, pose-based swing detection). Sweeping thresholds against the
existing source is a dead end, and re-fitting against audio-impact clusters is worse than
a dead end because that ground truth is the venue's activity.

## Tuning loop

Re-segmentation runs over cached features — no GPU, ~200 ms — so I can sweep freely:

```bash
bv segment <source_id> --dry-run                    # the source's own profile default
bv segment <source_id> --dry-run --threshold 0.30   # then sweep around it
```

Run the bare form first. The two profiles put the threshold on **different scales** —
0.45 for `pair`, 0.25 for `subject` — so a literal copied from one is meaningless in the
other, and the summary line reports which value was actually used. It prints each interval
as `index  start → end  (duration)  conf`. Read that against your memory of the session —
and note that on the one real source, `conf` is *anti*-correlated with whether the clip is
a real rally.

**The design is deliberately recall-biased**: rejecting a false rally costs one keystroke, but a missed rally is unrecoverable without rescrubbing an hour. So I want the threshold that *slightly over-segments*, not the cleanest one.

The UI also has a re-segment slider that shows the rally count live, plus a score curve under the timeline showing the detector's `score(t)` against the threshold line — so I can see *why* a cut landed where it did.

## Important: the play region

Without a court quad, detection runs on the whole frame and `w_outside` never fires, so players on adjacent courts get picked up as my opponent. (In `subject` mode `w_outside` never fires regardless — but the quad still decides which boxes reach the detector at all, so it matters there too.) On the session page there's an editor: drag four corners over the area both players move in, extended to the bottom of the frame (at 1 ft camera height the near player's box is clipped by the frame edge). Save & assign, then re-run detection:

```bash
bv detect <source_id> --now
```

## Scoring function, for interpreting results

There are two scoring profiles now, chosen automatically per source by
`detect/viewpoint.py::analyze_view` from the median vertical gap between the two
largest person boxes. Build params with `params_for_frames()` — never
`SegmentParams()` directly, or you silently get pair mode.

**`pair`** — camera high enough that the two players sit at visibly different depths:

```
score(t) = w_both·both_present
         + w_speed·min(near_v, far_v)        both players moving, not one retrieving a ball
         + w_lateral·lateral_fraction(near)  share of DISPLACEMENT that is horizontal
         + w_hits·hit_rate(t)                audio: ball contact in the trailing 1s
         + w_reg·hit_regularity(t)           audio: rallies are metronomic
         - w_outside·either_outside_region
```

`w_both=1.0, w_speed=0.9, w_lateral=0.3, w_hits=0.7, w_reg=0.4, w_outside=1.2,
threshold=0.45, close_gap_s=2.0`. **Never validated against real footage** — no
two-player source exists yet.

**`subject`** — camera low enough that the far half of the court collapses onto the
horizon. Presence is a gate rather than a term:

```
score(t) = 0                                             if no box >= subject_min_h
         = (w_speed·near_v + w_lateral·lateral
            + w_hits·hit_rate + w_reg·hit_regularity)
           / (w_speed + w_lateral + w_hits + w_reg)      otherwise
```

`threshold=0.25, close_gap_s=2.0`, `subject_min_h = 0.5 × median(near.h)`. The two
thresholds are **not comparable** — subject's denominator drops `w_both`.

**The 0.25 is a placeholder, not a fitted value.** See "Known weakness" below.

Post-processing (both profiles): rolling median (~1 s), threshold, close gaps < 2.0 s,
drop segments < 1.5 s (kept low deliberately — a 3 s floor discarded aces),
pad −0.3 s / +0.5 s.

Ignore synthetic-fixture scores when reasoning about any of this. The fixtures hand every
player `v=2.0`, roughly 8x anything measured on real footage, and calibrating against them
is what produced the one-hit-per-clip bug. Calibrate against
`tests/fixtures/ground_level_source01.jsonl`, which is a real slice.

## Likely failure modes and what they mean

- **Way too many short rallies** → threshold too low, or the play region is too generous and adjacent-court players are being counted.
- **Rallies merged together** → `close_gap_s` (2.0 s) too large, or the players never stop moving between points.
- **Long rallies split in two** → threshold too high, or a lob/lull drops the score below it mid-rally.
- **Every clip is one hit long** → the calibration bug fixed on 2026-08-20. `MAX_SPEED` was
  4.0 while real `min(near.v, far.v)` runs a median of 0.04, so the speed term contributed
  ~0.01 of its possible 0.27 and only the ~1 s audio-impact window ever cleared the
  threshold. Before touching weights, measure `min(near.v, far.v)` against `MAX_SPEED` on
  the actual footage.
- **Almost nothing detected** → check the play region first; if the quad is wrong, `n_in_region` is 0 and nothing can score. This applies in **both** profiles: `split_near_far` filters boxes by the quad before electing near/far, so a wrong quad starves `subject` mode too (`near is None` everywhere → score 0 everywhere). What `subject` mode drops is only the `w_outside` scoring term. Fixing a quad needs a full re-detect — `--reuse-features` reuses features that were already quad-filtered.
- **A ground-level source classified as `pair`** → the court quad is probably admitting adjacent courts, which inflates the measured foot separation. Check `analyze_view(frames)` directly.
- **Camera setup or teardown detected as a rally** → known and unfixed in `subject` mode. See "Known weakness" below.
- **Warmup detected as rallies** → that's intended. A rally is defined as continuous hitting; warmup counts.

## Known weakness: `subject` mode is unvalidated and known to be wrong

**Read `docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md` before you tune
anything here.** An earlier version of this file blamed a "far-player dropout" — YOLO
losing the far player on 38% of frames. That diagnosis was wrong. Those frames were not
dropouts: at a camera a foot off the ground the "far player" was **people on adjacent
courts**, and `split_near_far` was electing whichever stranger happened to be
second-largest.

Six detected intervals from the one real source were inspected frame by frame. One is an
unambiguous false positive — the operator walking back to stop recording. Two are real.
One (the opening clip) turned out to be *mixed*: camera setup for its first 8 seconds,
then genuine play. Two could not be settled from stills.

That mixed clip is worth knowing about, because the person writing this hand-labelled it
"camera setup, no play" and a pose track later proved otherwise. Hand labels are the thing
this project is shortest of, and they are not automatically right.

The cause is that neither of `subject` mode's two inputs carries signal on this footage:

- **Audio measures the venue, not the player.** Impacts fire at 0.56/s across windows a
  pose track confirms are swing-free, against 0.67/s while playing — and two of those idle
  windows individually run at 0.67/s and 0.80/s, above a confirmed rally's 0.65/s. Same at
  every prominence floor. Stereo does not help: both windows localise to a median GCC-PHAT
  lag of 1 sample, the mics being far too close together to resolve court distances.
  Neither does timbre: measured against the background immediately before each onset,
  brightness does not separate near hits from far ones.
- **Near-player motion barely separates.** In-rally vs between-point median lateral
  displacement is 0.0048 against 0.0032 per 200 ms. The player walks, retrieves balls and
  repositions between points, and that looks like playing.

`subject` mode ships enabled anyway, because on this footage it still beats the
alternative: `pair` mode produced 3.9 s one-hit clips at 22% coverage while scoring
strangers as the opponent. This codebase is recall-biased — rejecting a false rally is one
keystroke — so over-inclusion is the cheaper error. That is a judgement call, not a
validated result.

**What would actually fix it:** raise the camera. A fence-mounted phone puts the two
players at genuinely different depths, which is the assumption `pair` mode is built on and
the only configuration with a real signal to work with. `analyze_view` will detect the
change and switch profiles on its own. Tuning weights or thresholds against ground-level
footage will not help, and re-fitting against audio clusters actively misleads — that
ground truth is the venue's activity, not yours.

## Diagnostics

```bash
bv doctor                        # hardware + library paths
bv --library ... serve           # then http://127.0.0.1:8420
```

Inspect the DB directly:
```bash
sqlite3 /Volumes/SanDisk_2TB/SplitStep/library.db \
  "SELECT id, idx, start_ms, end_ms, confidence, starred FROM rallies ORDER BY idx;"
```

Feature stream for a source:
```bash
head -3 /Volumes/SanDisk_2TB/SplitStep/sessions/<date>/sources/01/features.jsonl
```
Each line: `{"t":ms,"n":players_in_region,"near":{...},"far":{...},"hits":n,"hit_reg":0-1}` where player `v` is speed in body-lengths/sec.

## State

- Both plans plus 4K clip export complete and merged to `master`. 471 Python tests, 319 TypeScript tests, ruff clean, svelte-check clean.
- Design rationale and decision log: `docs/superpowers/specs/2026-08-19-splitstep-design.md`
- Implementation plans: `docs/superpowers/plans/`
- **Still deferred:** reel building (concat with `-c copy`), cross-session rally browser.
- **Rejected:** Reclaim Space. The drive holds ~110 hours of play keeping every original, so there is nothing to reclaim, and deleting an original forfeits the 4K source for any rally not flagged at the time.

## What I want from you

Help me run a real session through this and interpret the results. Start by asking me what I actually observed — how many rallies it found, whether they line up with what happened, what the score curve looks like. Then help me pick a threshold and, if the weights themselves look wrong, work out which term is misbehaving.

Once a threshold looks right, that session's `features.jsonl` plus my hand-labeled intervals become a golden regression fixture in `tests/fixtures/` — and eventually the training set for a learned segmenter, since every boundary I drag records the delta between the detector's guess and the right answer in the immutable `det_start_ms`/`det_end_ms` columns.
