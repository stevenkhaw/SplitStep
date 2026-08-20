# BootlegVision — session handoff

Copy everything below into a new chat.

---

I'm working on **BootlegVision**, a local tennis video tool I built at `/Users/stevenkhaw/Documents/GitHub/BootlegVision`. It ingests phone footage of my tennis sessions, automatically cuts it into rallies, and lets me review them in a keyboard-driven pass — star the good ones, reject false positives, fix boundaries — then eventually compile starred rallies into highlight reels.

**The code is complete and merged to `master`. What is NOT done is validating it against real footage.** That's what I need help with.

## Setup

```bash
conda activate bootleg
cd /Users/stevenkhaw/Documents/GitHub/BootlegVision
```

Library lives on an external SSD: `/Volumes/SanDisk_2TB/BootlegVision` (1.8 TB free).

I have this alias:
```bash
alias bv="conda run -n bootleg bootleg --library /Volumes/SanDisk_2TB/BootlegVision"
```

Machine: M2 MacBook Air, 8 GB RAM. ffmpeg 9.0.1. torch 2.13 with MPS. `detect_accel()` returns `videotoolbox` / `h264_videotoolbox` / `mps`, so both transcode and inference are GPU-accelerated.

## How it works

- Drop a video in `<library>/_inbox/`. A watcher picks it up once the file size stops changing, moves it to `sessions/<date>/sources/NN/`, transcodes a 1080p H.264 proxy, then runs detection.
- **Detection is two-stage on purpose.** An expensive stage (YOLO11 person detection at 5 fps + audio impact detection) writes `features.jsonl`. A cheap pure function (`segment()`, ~200 ms) turns those features into rally intervals. That split exists so I can retune a threshold without re-running the GPU work.
- Review UI: `bv serve`, then http://127.0.0.1:8420. Queue mode autoplays rallies; `S` stars, `X` rejects, `U` undoes, `←`/`→` navigate, `1`/`2`/`3` set speed, `T` opens a timeline for fixing boundaries.

## The actual task

**The segmentation weights and threshold were tuned on synthetic signals only.** Nobody knows whether they produce sensible rallies on real footage. Three specific unknowns:

1. Do the thresholds find actual rallies at my camera angle (phone, roughly 1 ft off the ground, behind the baseline, on public courts with adjacent games in frame)?
2. Does audio impact detection survive wind? It's a secondary signal — an 800 Hz highpass plus a peakiness gate and a prominence floor. If it's useless the weights should fit to zero.
3. Does a 4K-derived proxy play and scrub acceptably in the browser?

## Tuning loop

Re-segmentation runs over cached features — no GPU, ~200 ms — so I can sweep freely:

```bash
bv segment <source_id> --dry-run --threshold 0.35
bv segment <source_id> --dry-run --threshold 0.45
bv segment <source_id> --dry-run --threshold 0.55
```

It prints each interval as `index  start → end  (duration)  conf`. I read that against my memory of the session.

**The design is deliberately recall-biased**: rejecting a false rally costs one keystroke, but a missed rally is unrecoverable without rescrubbing an hour. So I want the threshold that *slightly over-segments*, not the cleanest one.

The UI also has a re-segment slider that shows the rally count live, plus a score curve under the timeline showing the detector's `score(t)` against the threshold line — so I can see *why* a cut landed where it did.

## Important: the play region

Without a court quad, detection runs on the whole frame and `w_outside` — the largest single weight in the scoring function — never fires, so players on adjacent courts get picked up as my opponent. On the session page there's an editor: drag four corners over the area both players move in, extended to the bottom of the frame (at 1 ft camera height the near player's box is clipped by the frame edge). Save & assign, then re-run detection:

```bash
bv detect <source_id> --now
```

## Scoring function, for interpreting results

```
score(t) = w_both·both_present
         + w_speed·min(near_v, far_v)        both players moving, not one retrieving a ball
         + w_lateral·lateral_fraction(near)  share of DISPLACEMENT that is horizontal
         + w_hits·hit_rate(t)                audio: ball contact in the trailing 1s
         + w_reg·hit_regularity(t)           audio: rallies are metronomic
         - w_outside·either_outside_region
```

Defaults: `w_both=1.0, w_speed=0.9, w_lateral=0.3, w_hits=0.7, w_reg=0.4, w_outside=1.2, threshold=0.45`.

Post-processing: rolling median (~1 s), threshold, close gaps < 2.0 s, drop segments < 1.5 s (kept low deliberately — a 3 s floor discarded aces), pad −0.3 s / +0.5 s.

A synthetic active-rally frame scores **0.7909** against the 0.45 threshold — but that
number is close to meaningless, because the fixtures hand every player `v=2.0`, roughly
8x anything measured on real footage. On the first real source
(`sessions/2026-08-18/sources/01`, 19.5 min) the median *smoothed* score for a frame with
both players visible and no impact in the trailing second is **0.4119**, and only **43%**
of such frames clear the threshold. `MAX_SPEED` and `threshold` were recalibrated against
that source on 2026-08-20; the score still separates rally from non-rally far more weakly
than the synthetic figure suggests. See the comments in `detect/segment.py`.

## Likely failure modes and what they mean

- **Way too many short rallies** → threshold too low, or the play region is too generous and adjacent-court players are being counted.
- **Rallies merged together** → `close_gap_s` (2.0 s) too large, or the players never stop moving between points.
- **Long rallies split in two** → threshold too high, or a lob/lull drops the score below it mid-rally.
- **Every clip is one hit long** → the calibration bug fixed on 2026-08-20. `MAX_SPEED` was
  4.0 while real `min(near.v, far.v)` runs a median of 0.04, so the speed term contributed
  ~0.01 of its possible 0.27 and only the ~1 s audio-impact window ever cleared the
  threshold. Before touching weights, measure `min(near.v, far.v)` against `MAX_SPEED` on
  the actual footage.
- **Almost nothing detected** → check the play region first; if the quad is wrong, `n_in_region` is 0 and nothing can score.
- **Warmup detected as rallies** → that's intended. A rally is defined as continuous hitting; warmup counts.

## Known weakness: far-player dropout

The single biggest source of score noise is not the weights. On the first real source YOLO
finds a near player but no far one on **38%** of sampled frames, and `both` going false
zeroes the score outright *and* fires `w_outside`. Worse, when the far player is
re-acquired, `_to_player` sees `prev is None` and reports `v=0`, so `min(near.v, far.v)`
collapses to 0 for that frame regardless of how fast either player is moving — 22% of
frames that do have a far player report exactly `far.v == 0`.

This is why the 2026-08-20 retune leaned on `close_gap_s` as much as on `MAX_SPEED`: the
gaps are being bridged rather than scored correctly. Fixing the dropout (tracker, box
persistence across a frame or two, or carrying the last known far position) would do more
for segmentation quality than any further weight tuning.

## Diagnostics

```bash
bv doctor                        # hardware + library paths
bv --library ... serve           # then http://127.0.0.1:8420
```

Inspect the DB directly:
```bash
sqlite3 /Volumes/SanDisk_2TB/BootlegVision/library.db \
  "SELECT id, idx, start_ms, end_ms, confidence, starred FROM rallies ORDER BY idx;"
```

Feature stream for a source:
```bash
head -3 /Volumes/SanDisk_2TB/BootlegVision/sessions/<date>/sources/01/features.jsonl
```
Each line: `{"t":ms,"n":players_in_region,"near":{...},"far":{...},"hits":n,"hit_reg":0-1}` where player `v` is speed in body-lengths/sec.

## State

- Both plans complete and merged to `master`. 233 Python tests, 171 TypeScript tests, ruff clean, svelte-check clean.
- Design rationale and decision log: `docs/superpowers/specs/2026-08-19-bootlegvision-design.md`
- Implementation plans: `docs/superpowers/plans/`
- **Deferred to a future Plan 3:** 4K clip export, reel building (concat with `-c copy`), cross-session rally browser, Reclaim Space.

## What I want from you

Help me run a real session through this and interpret the results. Start by asking me what I actually observed — how many rallies it found, whether they line up with what happened, what the score curve looks like. Then help me pick a threshold and, if the weights themselves look wrong, work out which term is misbehaving.

Once a threshold looks right, that session's `features.jsonl` plus my hand-labeled intervals become a golden regression fixture in `tests/fixtures/` — and eventually the training set for a learned segmenter, since every boundary I drag records the delta between the detector's guess and the right answer in the immutable `det_start_ms`/`det_end_ms` columns.
