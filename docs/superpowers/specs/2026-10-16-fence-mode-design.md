# Fence Mode — Mount and Format Selectors, and a Detector for the Fence Mount

2026-10-16. Status: design, awaiting approval.

## Why

Every session from now on is filmed from one place: the phone clipped to the
fence **centred behind the near baseline at 5–6 ft**, looking down the length
of the court. Steven tried to review 2026-10-12 (82 min, source
`ae5e11fc8cee4dd19e6c88bb8240428f`) and gave up: whatever threshold he chose,
clips either started after the serve or ran on long after the point, and many
points were missing. He rejected 76 of 133 clips and stopped correcting
because "they were all off and wrong".

That is the third fence-mount session to fail, each with a different symptom
of one cause (2026-08-25: `both_present` saturated; 2026-09-16: pair mode
hard-zeroed one-player frames; 2026-10-12: below). The detector guesses the
camera viewpoint and then scores with hand-picked weights that were never
fitted to this camera.

## What was measured (2026-10-16)

Ground truth: Steven hand-listed every point in the first 16 minutes of
2026-10-12 — 38 rows, committed as `tests/fixtures/truth/2026-10-12_source01.tsv`
(`in`, `out`, `note`, `server`, `score_after`; times `m:ss`). Play is **25%**
of those 16 minutes. 15 rows are `missed first serve` (a fault), 3 are
`missed second serve` (a double fault, which ends the point).

**Today's detector is barely better than chance.** `analyze_view` picks
`pair`, and near+far are "present" in 99.6% of frames, because "far" is
"second-largest box in the quad" and the quad (top edge at y≈0.31, bottom
corners 0.02/0.98) admits 4–7 people per frame: bags and a person at the
right net post (≈ cx 0.68, foot 0.48), and the next courts. Far-box identity
swaps produce speeds of 10–48 body-lengths/s. Idle frames score 0.36–0.43,
play 0.40–0.66; the median of the whole source is 0.45.

| detector (first 16 min) | points caught (≥50% covered) | clip time that is play | median start error | median end error |
|---|---|---|---|---|
| pair 0.40 (what Steven used) | 34/38 | 28% | −0.3 s | **+9.7 s** |
| pair 0.45 | 21/38 | 33% | +1.7 s | +5.5 s |
| pair 0.50 | 8/38 | 37% | +3.7 s | +2.5 s |
| subject 0.25 | 16/38 | 34% | +1.7 s | +2.9 s |

**Single signals, per-frame AUC against the truth** (0.5 = coin flip). Raw
YOLO boxes were dumped for the 16 minutes (all persons and sports balls, conf
≥ 0.1) so player selection could be varied without re-detecting:

| signal | AUC |
|---|---|
| audio hits in the trailing second (what `segment` uses today) | 0.59 |
| audio hits in the **next** 4–8 s | **0.83** |
| near player box-top jitter, 1 s (arm/racket movement) | 0.76 |
| near player speed, 4–8 s window | 0.73–0.74 |
| far player (tracked inside the far half of *our* court) speed | 0.58 |
| YOLO sports-ball detections | 0.53 |

The forward-looking audio result is the "clips start after the serve"
complaint in numbers: a point starts several seconds before the first
impact the detector hears, and today's score only looks backwards.

**Combined:** a logistic regression over ~60 rolling-window features of the
above (near/far speed and lateral speed, box-top and aspect jitter, audio
hits past/centred/future, ball detections, presence), trained with 4-minute
blocks held out, scores **0.86 AUC on held-out minutes**. Segmented
(smooth 1 s, threshold 0.2, close 1 s gaps, drop < 2 s, pad 0.5 s / 0.7 s):

| | points caught | clip time that is play | start err | end err |
|---|---|---|---|---|
| fence model, th 0.2 | 34/38 | **53%** | −1.3 s (at the toss) | +1.1 s |

The four misses are all first-serve faults; every real point was caught.
With faults folded into the following point (Steven's choice, below) and a
"short clip followed by another within 15 s merges into it" rule: 34 clips
for 23 points, 67% of clip time is play.

**The caveat that matters most:** this is one session, one opponent, one
evening's light. Held-out blocks prove it is not memorising individual
minutes; they do not prove it carries to the next session. The next
labelled fence session is the real test (see Validation).

## Goals

1. Two selectors per source in the setup wizard, both persisted, both
   editable later:
   - **Mount:** Fence / Ground / Not set.
   - **Format:** Singles / Doubles / Drills / Not set.
2. A detector for **Fence + Singles** that beats today's on the committed
   truth file, measured, not eyeballed.
3. Faults **fold into the point that follows**: one clip per point, starting
   at the first serve's toss.
4. A reproducible scoring harness: any change to fence detection is scored
   against every committed truth file, and a regression is visible.

## Non-goals

- Tuned detection for Fence + Doubles, Fence + Drills, or anything on Ground.
  Those combinations are selectable now and fall back (see Behaviour); they
  get tuned once footage exists.
- Training inside the app. The model is fitted offline by a CLI command and
  its weights are committed as package data.
- A new in-app labelling tool. Truth files are typed TSVs for now.
- Changing `pair`/`subject` scoring or their thresholds. Fence mode is a
  third path beside them, not a retune of either (the 2026-08-20 validation
  doc's prohibition stands).
- Pose models, ball tracking, or any new model download.

## Anti-goals (failure even if it "works")

- Fitting against audio-impact clusters or the detector's own output. Ground
  truth is Steven's hand list and nothing else.
- Reporting a single score that hides which truth file or which metric it
  came from. Every number names its file, its threshold and its denominator.
- Silently changing rallies on sources nobody re-detected. Existing sources
  keep their rallies until a human runs detection.
- Pretending an untuned combination is tuned.

## Behaviour

### Selectors

- Live on the setup wizard's existing step, beside rotation and the court
  outline. Three buttons for mount, four for format.
- Stored per source: migration `018` adds `sources.mount TEXT NOT NULL
  DEFAULT ''` (`''`, `'fence'`, `'ground'`) and `sources.play_format TEXT NOT
  NULL DEFAULT ''` (`''`, `'singles'`, `'doubles'`, `'drills'`). `''` is
  "Not set", and every existing source starts there, so nothing old changes.
- The wizard pre-selects the **most recently set** values in the library
  (newest source with a non-empty value), so Steven normally just confirms
  Fence + Singles. *(My call: derived from data, no config key to drift.)*
- Validated in `queue_setup` alongside rotation, so HTTP and CLI share it;
  the `splitstep setup` CLI gains `--mount` and `--format`.
- Editable after detection. Changing either one on a processed source is a
  change to what detection needs, exactly like a changed quad: the Detection
  panel shows it and offers a re-detect (or a re-segment, when the cached
  boxes suffice — see Pipeline). It never re-runs on its own.

### Which scorer runs

| mount | format | scorer |
|---|---|---|
| fence | singles | **fence model** |
| fence | doubles / drills / not set | today's auto (`params_for_frames`), with a visible "not tuned for fence + {format} yet" note |
| ground | any | `subject` profile forced (skip `analyze_view`'s guess) |
| not set | any | today's auto, unchanged |

*(My call: Ground forces `subject` because that is what Ground footage was
classified as when the classifier was right, and forcing it removes the
guess that pushed ground sources into `pair` when a loose quad admitted
neighbours. It is still unvalidated, and the UI says nothing to the
contrary.)*

### The court outline in fence mode

The fence model needs **our court**, not a loose play region: the far half
(net to a margin behind the far baseline) is where the opponent is looked
for, and the near half is where the near player is. In fence mode the
wizard asks for the four **court corners** (doubles sidelines × baselines)
rather than "a region", with copy that says so; a homography from those
corners to court metres is what decides near half / far half / outside.
*(My call: the 2026-10-16 measurement used hand-picked image bands — far
feet y 0.405–0.47, cx 0.40–0.665 — which only fit this one camera
placement. Corners generalise to the next clip-on position.)* Corners are
stored separately from the play region (Settled 3); 2026-10-12 has only a
loose region today, so its corners must be drawn before it is re-detected in
fence mode.

### Faults fold in

After segmentation, a clip ≤ 6 s followed by another clip starting within
15 s is merged into it. The merged clip starts at the first clip's start.
Both constants are measured on the truth file and named, with that
provenance, in a comment. The truth loader folds `missed first serve` rows
into the following row the same way so scoring compares like with like.

## Pipeline

- **Detection keeps every box.** The detect job writes `boxes.jsonl` beside
  `features.jsonl`: per 200 ms frame, every person and sports-ball box
  (class, conf, x1 y1 x2 y2 normalised), conf ≥ 0.1. It is the expensive
  stage's real output; player selection becomes cheap and re-runnable.
  ~4800 lines per 16 min, small next to the proxy. YOLO is asked for
  classes `[0, 32]` at conf 0.1 (today: `[0]` at the default 0.25). Its
  effect on detect time was not measured against today's; the 16-minute
  spike run took ~10 min, comparable to the usual detect rate.
- **Fence features** are computed from `boxes.jsonl` + the audio grid + the
  court corners: tracked near player (largest box in the near half, then
  nearest-neighbour), tracked far player (inside the far half), and the
  rolling-window features listed in Measured. This is a pure function, like
  `segment()`, so a re-segment after a corner edit costs no GPU when
  `boxes.jsonl` exists.
- **The fence model** is a logistic regression: feature list, means,
  standard deviations, weights, bias, threshold, fold constants — one JSON
  file in `splitstep/assets/fence_model.json`, declared as package data and
  collected into the freeze (the migrations lesson applies: check the freeze
  contains it).
- **Training is a CLI command**, e.g. `splitstep fence train`, reading every
  `tests/fixtures/truth/*.tsv` whose source has `boxes.jsonl`, fitting with
  held-out time blocks, printing the held-out metrics per truth file, and
  writing the JSON. Plain numpy; no scikit-learn dependency.
- **Scoring is a CLI command**, e.g. `splitstep fence score`, printing for
  each truth file: points caught, clip time that is play, median start and
  end error, the list of missed points. Same matching rule as above (a truth
  point is caught when detected clips cover ≥ 50% of it). Named exactly that
  way, never "recall" alone.
- Segment threshold for a fence source is recorded in
  `sources.segment_threshold` like every other path, and the re-segment
  slider works on the model's probability scale (default from the model
  file).
- `rally_labels` and the blind audit pass are untouched and still work.

## Validation (what "done" means)

1. On `2026-10-12_source01.tsv`, held-out (as defined in Settled 1): every
   non-fault row caught (23/23) and ≥ 34/38 rows overall, clip time that is
   play ≥ 50%, median end error ≤ 2 s. These are the 2026-10-16 spike numbers; the
   productionised version must not be worse.
2. Steven re-reviews 2026-10-12 in the app after a fence re-detect and says
   whether it is usable. That is the bar the numbers stand in for.
3. **Not part of this build, but the next step:** a truth list for the first
   ~16 minutes of the next fence session, scored *before* retraining. That
   number is the first honest measure of whether the model transfers. Until
   then the UI and docs call fence mode "fitted on one session".

## Constraints

- Everything in `CLAUDE.md`'s Conventions applies: pytest
  `filterwarnings=error`, ruff 100, no YOLO in tests (fixtures/mocks only),
  migrations append-only, logic in `web/src/lib/` not `.svelte`, tokens from
  `app.css`.
- A test fixture: a committed slice of `boxes.jsonl` for (part of) the 16
  minutes, plus the matching audio hits, so feature extraction, the model
  and the fold rule are tested against real footage, never synthetics.
- The 82-minute re-detect is ~30+ min of GPU and is Steven's to start.

## Settled after review (my calls, marked as such)

1. **Two scoring tools, named for what they measure.**
   `splitstep fence train` fits and, in the same routine, reports
   **held-out** metrics: for every truth file, leave out 4-minute blocks,
   refit on the rest (all other truth files included), predict the
   left-out block. That held-out report is what Validation 1 and Goal 4
   are measured with. `splitstep fence score` applies the committed JSON
   to a source and labels its output **in-sample** whenever that truth file
   was in the training set — a quick check, never the bar.
   Both report **raw** (38 rows) and **folded** (23 points) figures. The bar
   is on raw rows: every non-fault row caught (23/23), ≥ 34/38 overall.
2. **Data before Steven re-detects.** The spike's box dump is committed as
   `tests/fixtures/boxes/2026-10-12_source01_0-16min.jsonl` (rackets
   stripped, classes 0 and 32 only, 2.2 MB, same format as the planned
   `boxes.jsonl`). The spike scripts are kept in
   `docs/superpowers/spikes/2026-10-16-fence/` as provenance. Building and
   testing the model, features, fold rule and harness use the fixture; only
   Validation 2 waits on Steven drawing corners and starting the re-detect.
   The fixture's coordinates are fixed, so the fixture also commits the
   court corners for this camera placement (read off a frame by the
   builder, checked by Steven in the wizard before his re-detect).
3. **Court corners are their own stored thing.** Not a reuse of the play
   region: migration `018` also adds `sources.court_corners TEXT` (JSON,
   four points in a fixed order — near-left, near-right, far-right,
   far-left — NULL when not drawn). The play-region quad stays exactly what
   it is for every other scorer. In fence mode the wizard asks for corners
   and **derives** the region from them (corners expanded by a stated
   margin behind each baseline and beside each sideline), so Fence + Doubles
   / Drills keep running today's scorer on a region rather than the bare
   court lines. Fence + Singles refuses to queue without corners, with a
   sentence saying so — never silently treating a loose region as corners.
4. **Corners win over the hand-picked bands.** The bands fit one clip-on
   position; corners are the point. If the corner-based version lands below
   the bar, the builder may tune the margins against the truth file, and
   must report the shortfall rather than fall back to bands.
5. **A truth file says what it covers.** First line `# covers m:ss-m:ss`
   (added to the 2026-10-12 file: `# covers 0:00-16:00`). The loader
   refuses a file without it. Time outside the range is neither play nor
   idle and is excluded from every number.

## Left to the builder

- Exact homography and margin behind the far baseline; how to handle a
  corner click order.
- Exact feature list (start from the spike's ~60; drop what carries no
  weight), regularisation, and the CLI names.
- How the Detection panel words the mount/format-changed state.
- Whether `boxes.jsonl` is written for non-fence sources too (recommended:
  yes, it costs nothing and makes a later switch to Fence a re-segment).
