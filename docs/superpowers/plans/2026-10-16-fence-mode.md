# Fence Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-source Mount and Format selectors, and a detector for Fence + Singles fitted to Steven's hand-listed points, that beats today's scorer on held-out minutes.

**Architecture:** Detection keeps every YOLO box in a new `boxes.jsonl` beside `features.jsonl`. A pure fence pipeline (court homography from four drawn corners → tracked near/far players → rolling features → committed logistic model → threshold/close/fold) turns boxes + audio hits into intervals in milliseconds, like `segment()`. One dispatcher, `scorer.segment_source`, picks fence / forced-subject / today's auto from the source's mount and format, and every caller that segments goes through it.

**Tech Stack:** Python 3.12, numpy (no scikit-learn), sqlite migrations, FastAPI, Svelte 5 + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-10-16-fence-mode-design.md` — read it first; its "Measured" tables are the bar and its "Settled after review" section decides the ambiguous parts.

## Global Constraints

- pytest runs with `filterwarnings = ["error"]`; ruff line-length 100. Interpreter: `~/miniconda3/envs/splitstep/bin/python -m pytest` (in a worktree, `-m` is required or master's code is imported).
- YOLO never runs in tests. Real-footage fixtures only for tuning: `tests/fixtures/boxes/2026-10-12_source01_0-16min.jsonl`, `tests/fixtures/features/2026-10-12_source01_0-16min.jsonl`, `tests/fixtures/truth/2026-10-12_source01.tsv`. Never fit or tune against synthetic frames.
- Migrations are append-only; the new one is `splitstep/db/migrations/018_mount_format.sql`.
- `mount` ∈ `''`, `'fence'`, `'ground'`; `play_format` ∈ `''`, `'singles'`, `'doubles'`, `'drills'`. `''` renders as "Not set".
- Court corners order: near-left, near-right, far-right, far-left, normalised to the proxy frame (post-rotation). Doubles court is 10.97 m × 23.77 m; net at 11.885 m.
- Numbers name their metric: "points caught", "clip time that is play", "median start error", "median end error". Never bare "recall".
- Anything read at runtime by path is package data and must be in the freeze: `splitstep/assets/fence_model.json`.
- Frontend logic lives in `web/src/lib/`; components are thin. Colours/sizes only from `app.css` tokens.
- Comments explain why, at the codebase's density. Existing sources (mount `''`) must segment exactly as today.

## Review Focus

1. **A Fence + Singles source detected before this change has no `boxes.jsonl`.** Re-segment and `/scores` must refuse with a sentence ("re-run detection to use fence mode"), not 500 — test in Task 7.
2. **Corners drawn crossed or out of order** (self-intersecting, or the far edge below the near edge) must be refused at setup with a sentence, never produce a mirrored homography — test in Task 4 (`order_corners` / validation) and Task 8 (the route).
3. **A source with no usable audio** (empty audio grid, all `hits == 0`) must still produce fence intervals from vision alone, no NaN — test in Task 5.
4. **A stretch with zero detections** (camera knocked, lens covered) must give finite probabilities and no interval there — test in Task 5.
5. **A rotated source**: boxes come from the rotated proxy and corners are drawn on the rotated preview, so both are in the same space; nothing may re-apply `rotation_deg` to either — test in Task 4 asserting `court_xy` takes proxy coordinates unchanged.

---

### Task 1: Migration 018 and the source columns

**Files:**
- Create: `splitstep/db/migrations/018_mount_format.sql`
- Modify: `splitstep/db/sessions.py` (`set_source_setup` near line 348, add `set_source_scorer`)
- Test: `tests/test_mount_format_db.py`

**Interfaces:**
- Produces: columns `sources.mount TEXT NOT NULL DEFAULT ''`, `sources.play_format TEXT NOT NULL DEFAULT ''`, `sources.court_corners TEXT` (JSON or NULL), `sources.detected_scorer TEXT` (NULL, `'fence'`, `'subject'`, `'pair'`).
- Produces: `set_source_setup(conn, source_id, rotation_deg, preset_id, *, mount: str = '', play_format: str = '', court_corners: list[tuple[float, float]] | None = None) -> None` — one UPDATE for all five columns; validates `mount`/`play_format` membership (raises `ValueError`).
- Produces: `set_source_scorer(conn, source_id, scorer: str) -> None`.
- Produces: `latest_setup_choices(conn) -> dict` → `{"mount": str, "play_format": str}` from the newest source (by `recorded_at`) with a non-empty value, each independently; `''` when none.

- [ ] **Step 1: Write failing tests** — `test_new_columns_default_to_not_set` (fresh library: `mount == ''`, `play_format == ''`, `court_corners IS NULL`, `detected_scorer IS NULL`); `test_set_source_setup_writes_mount_format_corners` (round-trips JSON corners as 4 pairs); `test_set_source_setup_rejects_unknown_mount` (`'tripod'` → `ValueError`, row unchanged); `test_latest_setup_choices_takes_newest_non_empty` (two sources, older fence/singles, newer `''`/doubles → `{"mount": "fence", "play_format": "doubles"}`).
- [ ] **Step 2: Run, verify they fail** — `~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_mount_format_db.py -q` → FAIL (no such column).
- [ ] **Step 3: Write the migration** with a rationale comment: why `''` not NULL for the two choices (Not set is a real choice, and existing rows must read as it), why corners are separate from `court_preset_id` (spec, Settled 3), why `detected_scorer` exists (the panel compares what cut the rallies with what would cut them now, the same idea as `features_preset_id` in 016).
- [ ] **Step 4: Implement the three functions** in `splitstep/db/sessions.py`; keep existing callers of `set_source_setup` working via the keyword defaults.
- [ ] **Step 5: Run the file and the full suite** — `-m pytest -q` → all pass (migration count tests included).
- [ ] **Step 6: Commit** — `feat(db): mount, format, court corners and detected scorer per source (018)`.

---

### Task 2: Truth files and the scoring report

**Files:**
- Create: `splitstep/detect/truth.py`
- Test: `tests/test_truth.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) TruthPoint(start_ms: int, end_ms: int, note: str, server: str)`; `@dataclass(frozen=True) Truth(covers: tuple[int, int], points: tuple[TruthPoint, ...])`.
- Produces: `load_truth(path: Path) -> Truth` — first line must be `# covers m:ss-m:ss` (else `ValueError` naming the file); tab-separated header `in out note server score_after`; times `m:ss` or `h:mm:ss`.
- Produces: `fold_faults(truth: Truth) -> Truth` — a row whose note starts with `missed first serve` merges into the following row (start of the fault, end of the next row); a trailing fault stays alone.
- Produces: `@dataclass(frozen=True) TruthReport(caught: int, total: int, play_share: float, median_start_err_s: float, median_end_err_s: float, missed: tuple[int, ...])` and `evaluate(intervals: Sequence[Interval], truth: Truth) -> TruthReport` — intervals clipped to `covers`; a point is caught when intervals cover ≥ 50% of it; start/end error are (earliest overlapping start − point start), (latest overlapping end − point end) over caught points; `play_share` is overlap with any point ÷ total interval time inside `covers`; `missed` is the start_ms of each missed point.
- Produces: `format_report(name: str, label: str, report: TruthReport) -> str` — one line using the spec's metric names.

- [ ] **Step 1: Write failing tests** with the committed file: `test_load_truth_reads_covers_and_38_points` (`covers == (0, 960000)`, `len(points) == 38`, first point `(33000, 37000)`); `test_load_truth_refuses_file_without_covers` (tmp file without the line → `ValueError`); `test_fold_faults_gives_23_points` (`len(fold_faults(t).points) == 23`, first folded point `(33000, 46000)`); `test_evaluate_perfect_intervals` (intervals equal to the points → caught 38/38, play_share 1.0, errors 0.0); `test_evaluate_counts_half_coverage_as_caught` (interval covering exactly 50% of a 4 s point → caught); `test_evaluate_ignores_time_outside_covers` (an interval at 20:00 changes neither play_share nor caught).
- [ ] **Step 2: Run, verify they fail** — `-m pytest tests/test_truth.py -q`.
- [ ] **Step 3: Implement** `splitstep/detect/truth.py` (reference: `docs/superpowers/spikes/2026-10-16-fence/truth.py`, whose `evaluate` defines the matching rule).
- [ ] **Step 4: Run, verify they pass.**
- [ ] **Step 5: Commit** — `feat(detect): truth files with a covers line, and the fence scoring report`.

---

### Task 3: Keep every box — `boxes.jsonl`

**Files:**
- Create: `splitstep/detect/boxes.py`
- Modify: `splitstep/detect/vision.py` (`iter_person_boxes` → `iter_detections`; keep `iter_person_boxes` as the legacy filter)
- Modify: `splitstep/jobs/handlers.py::handle_detect` (write `boxes.jsonl` on the full-detect branch)
- Test: `tests/test_boxes.py`, extend `tests/test_handlers_detect*.py` (whichever file covers `handle_detect`)

**Interfaces:**
- Produces: `@dataclass(frozen=True) Detection(cls: int, conf: float, x1: float, y1: float, x2: float, y2: float)` with properties `cx`, `foot` (= y2), `h`, `w`, `top` (= y1).
- Produces: `PERSON = 0`, `BALL = 32`, `DETECT_CONF = 0.1`, `LEGACY_PERSON_CONF = 0.25`.
- Produces: `write_boxes(path: Path, frames: Iterable[tuple[int, list[Detection]]]) -> None`, `read_boxes(path: Path) -> list[tuple[int, list[Detection]]]` — the fixture's line format `{"t": ms, "b": [[cls, conf, x1, y1, x2, y2], ...]}`, floats rounded to 4 dp (conf 3 dp) so round trips are byte-stable.
- Produces: `persons_as_boxes(dets: list[Detection]) -> list[Box]` — persons with `conf >= LEGACY_PERSON_CONF` converted to today's `Box`, so `build_features` and every non-fence source see exactly the boxes they see today.
- Produces: `iter_detections(proxy: Path, *, sample_fps: int = 5, imgsz: int = 960) -> Iterator[list[Detection]]` — YOLO with `classes=[PERSON, BALL]`, `conf=DETECT_CONF`.
- Produces: `src_dir / "boxes.jsonl"` after every full detect, for every mount (spec, Left to the builder: recommended yes).

- [ ] **Step 1: Write failing tests** — `test_fixture_round_trips_byte_stable` (read the committed boxes fixture, write to tmp, bytes identical after one normalising write); `test_persons_as_boxes_drops_balls_and_low_conf` (a ball and a 0.2-conf person are dropped; a 0.3 person becomes a `Box` with the same cx/foot/h); `test_detect_writes_boxes_jsonl` (mock `iter_detections` as the existing detect tests mock `iter_person_boxes`; after `handle_detect`, `boxes.jsonl` exists with one line per frame, and `features.jsonl` equals what the old path produced from the same persons).
- [ ] **Step 2: Run, verify they fail.**
- [ ] **Step 3: Implement** `boxes.py`, `iter_detections` (the YOLO loop moves here; `iter_person_boxes` becomes `persons_as_boxes` over it), and the handler change: collect detections once, `write_boxes`, build features from `persons_as_boxes`. Update the existing tests' mock target.
- [ ] **Step 4: Run the full suite** → all pass.
- [ ] **Step 5: Commit** — `feat(detect): keep every person and ball box in boxes.jsonl`.

---

### Task 4: Court corners and the homography

**Files:**
- Create: `splitstep/detect/court.py`
- Create: `tests/fixtures/truth/2026-10-12_source01.corners.json` — four points read off a proxy frame of 2026-10-12 (e.g. `ffmpeg -ss 87` on the proxy, which shows the whole court); near baseline ≈ y 0.61 spanning x ≈ 0.20–0.92, net ≈ y 0.45, far baseline ≈ y 0.43. Steven re-checks these in the wizard before his re-detect.
- Test: `tests/test_court.py`

**Interfaces:**
- Produces: `COURT_W = 10.97`, `COURT_L = 23.77`, `NET_Y = 11.885`.
- Produces: `@dataclass(frozen=True) Court(corners: tuple[tuple[float, float], ...])` with `from_json(s: str) -> Court`, `to_json() -> str`, `court_xy(x: float, y: float) -> tuple[float, float]` (metres; image point in proxy space → court plane; near-left corner is (0, 0), far-right is (10.97, 23.77)), and `zone(x, y) -> Literal["near", "far", "outside"]`.
- Produces: margins `NEAR_BACK_M`, `FAR_BACK_M`, `SIDE_M` (builder's values, stated in a comment; spike bands correspond to roughly 4 m behind each baseline and 1.5 m beside each sideline).
- Produces: `validate_corners(points) -> tuple[tuple[float, float], ...]` — refuses non-4, out-of-[0,1], self-intersecting, or far edge not above near edge, with a sentence each.
- Produces: `region_from_corners(court: Court) -> Quad` — the court plus margins, back-projected to image space; what Fence + Doubles/Drills hand today's scorer.

- [ ] **Step 1: Write failing tests** — `test_corners_map_to_court_corners` (each fixture corner → its metre corner within 1e-6); `test_net_midpoint_maps_to_net` (midpoint of the two sideline-net intersections lands at y ≈ 11.885); `test_zone_classifies_fixture_players` (from the boxes fixture at t=87000: the server's foot is `near`; a box at cx≈0.57, foot≈0.44 is `far`; the net-post box at cx≈0.69, foot≈0.48 is `outside`); `test_validate_rejects_crossed_corners` and `test_validate_rejects_far_below_near` (Review Focus 2); `test_court_xy_uses_proxy_space_unchanged` (Review Focus 5: no rotation argument exists, and a 180°-rotated source's fixture corners map identically); `test_region_from_corners_contains_court_and_margin`.
- [ ] **Step 2: Run, verify they fail.**
- [ ] **Step 3: Implement** with a 3×3 homography solved by numpy from the four correspondences (DLT, 8 unknowns), and its inverse for `region_from_corners`.
- [ ] **Step 4: Run, verify they pass.**
- [ ] **Step 5: Commit** — `feat(detect): court corners and a homography to court metres`.

---

### Task 5: Fence features, model and segmentation

**Files:**
- Create: `splitstep/detect/fence.py`
- Test: `tests/test_fence.py`

**Interfaces:**
- Consumes: `Detection`, `read_boxes` (Task 3); `Court` (Task 4); `FeatureFrame.hits` from `features.jsonl`; `Interval` from `detect/segment.py`.
- Produces: `fence_features(frames: Sequence[tuple[int, list[Detection]]], hits: Sequence[int], court: Court) -> tuple[np.ndarray, tuple[str, ...]]` — one row per frame, finite everywhere. Tracks: near = largest person in `zone == "near"`, then nearest-neighbour; far = person in `zone == "far"`, nearest-neighbour; speeds in body-lengths/s clipped at 3. Features over windows of 5/10/20/40 frames: near and far speed and lateral speed, near box-top and aspect rolling std, audio hits centred / trailing / **leading** (the 0.83 AUC signal), ball count, near/far presence. Start from the spike's list in `docs/superpowers/spikes/2026-10-16-fence/learn.py`; drop features whose fitted weight is ~0.
- Produces: `@dataclass(frozen=True) FenceModel(features: tuple[str, ...], mean: tuple[float, ...], std: tuple[float, ...], weights: tuple[float, ...], bias: float, threshold: float, smooth_s: float, close_gap_s: float, min_duration_s: float, pad_start_s: float, pad_end_s: float, fold_max_s: float, fold_within_s: float, trained_on: tuple[str, ...])` with `load(path) / save(path)`; `predict(X) -> np.ndarray` (probabilities). `load` refuses a file whose `features` differ from what `fence_features` produces, with a sentence.
- Produces: `fence_segment(t_ms: Sequence[int], probs: np.ndarray, model: FenceModel, threshold: float | None = None) -> list[Interval]` — smooth, threshold, close gaps ≤ `close_gap_s`, drop < `min_duration_s`, pad, then **fold**: a clip ≤ `fold_max_s` (6 s) followed by one starting within `fold_within_s` (15 s) merges into it, keeping the first start. Constants carry a comment citing the spec's measurement.
- Produces: `fence_probs(frames, hits, court, model) -> np.ndarray`.

- [ ] **Step 1: Write failing tests** — `test_features_finite_and_aligned` (fixture boxes + fixture hits + fixture corners → shape `(4800, len(names))`, `np.isfinite(X).all()`); `test_features_without_audio_are_finite` (hits all zero → finite, Review Focus 3); `test_empty_stretch_gives_no_interval` (blank out detections for 60 s in a copy → no interval inside it and probs finite, Review Focus 4); `test_fold_merges_fault_into_next_point` (synthetic probs: a 4 s run, 10 s gap, an 8 s run → one interval from the first start); `test_fold_leaves_long_clips_apart` (a 9 s run then a 10 s gap → two intervals); `test_model_load_refuses_feature_mismatch`.
- [ ] **Step 2: Run, verify they fail.**
- [ ] **Step 3: Implement** `fence.py`.
- [ ] **Step 4: Run, verify they pass.**
- [ ] **Step 5: Commit** — `feat(detect): fence features, model file and fault-folding segmentation`.

---

### Task 6: Training, the held-out report, and the committed model

**Files:**
- Create: `splitstep/detect/fence_train.py`
- Create: `splitstep/assets/fence_model.json` (written by the command, committed)
- Modify: `splitstep/cli.py` (`fence train`, `fence score` subcommands), `pyproject.toml` package-data (`assets/*.json`), `packaging/build_app.sh` (guard: the freeze contains `fence_model.json`, beside the font guard)
- Test: `tests/test_fence_train.py`, extend `tests/test_packaging*.py` / wherever the font package-data test lives

**Interfaces:**
- Consumes: Tasks 2, 4, 5.
- Produces: `@dataclass TrainingSet(name: str, t_ms: np.ndarray, X: np.ndarray, y: np.ndarray, truth: Truth)`; `training_set(boxes: Path, features: Path, truth: Path, corners: Path) -> TrainingSet` — only frames inside `truth.covers`; labels from **raw** (unfolded) points.
- Produces: `fit(sets: Sequence[TrainingSet], *, l2: float = 1.0) -> FenceModel` — standardise, gradient-descent logistic regression in numpy; segmentation constants from the spec.
- Produces: `held_out(sets, *, block_ms: int = 240_000) -> dict[str, tuple[TruthReport, TruthReport]]` — per set, per 4-minute block: refit on everything else (other sets included), predict the block, segment; returns (raw, folded) reports over the whole covered range.
- Produces CLI: `splitstep fence train [--out PATH]` — discovers every `tests/fixtures/truth/*.tsv` with matching `.corners.json` and fixture boxes/features (or library paths given by flags), prints the held-out raw and folded line per set via `format_report`, writes the model. `splitstep fence score <source_id>` — applies the committed model to a library source and prints the line, suffixed `(in-sample)` when that source's truth file is in `trained_on`.

- [ ] **Step 1: Write failing tests** — `test_held_out_meets_the_spec_bar` (on the committed fixture set: raw report has every non-fault point caught — 23/23 of the rows not starting `missed first serve` — and `caught >= 34` of 38, `play_share >= 0.50`, `median_end_err_s <= 2.0`); `test_committed_model_matches_fixture_features` (`FenceModel.load` of `splitstep/assets/fence_model.json` succeeds and `trained_on` names `2026-10-12_source01`); `test_fence_model_is_package_data` (mirrors the font's test).
- [ ] **Step 2: Run, verify they fail.**
- [ ] **Step 3: Implement** `fence_train.py` and the CLI. If the corner-based held-out result misses the bar, tune margins (Task 4) against the file and report; do **not** fall back to hand-picked image bands (spec, Settled 4). If it still misses, stop and report the numbers.
- [ ] **Step 4: Run `splitstep fence train`**, commit its printed output into the commit message, verify the test file passes and the full suite passes.
- [ ] **Step 5: Commit** — `feat(detect): fence model fitted on 2026-10-12, with a held-out report`.

---

### Task 7: One dispatcher for every caller that segments

**Files:**
- Create: `splitstep/detect/scorer.py`
- Modify: `splitstep/jobs/handlers.py::handle_detect`, `splitstep/api/routes.py` (`api_resegment` ~1175, `api_scores` ~1255, `_source_json` ~492), `splitstep/cli.py` (`segment` and `labels score` paths that call `params_for_frames` at ~329 and ~429)
- Test: `tests/test_scorer.py`, extend route tests for resegment/scores

**Interfaces:**
- Produces: `resolve_scorer(mount: str, play_format: str) -> Literal["fence", "subject", "auto"]` — the spec's table: fence+singles → `"fence"`; ground+any → `"subject"`; everything else → `"auto"`.
- Produces: `@dataclass(frozen=True) Segmentation(intervals: list[Interval], threshold: float, scorer: str, step_ms: int, scores: list[float])` where `scorer` is the concrete one used (`"fence"`, `"subject"`, `"pair"`).
- Produces: `segment_source(source: Mapping, src_dir: Path, *, threshold: float | None = None) -> Segmentation` — `"fence"` reads `boxes.jsonl`, `features.jsonl` hits and `source["court_corners"]`; raises `FenceUnavailable(sentence)` when `boxes.jsonl` or corners are missing. `"subject"` builds `SegmentParams(profile="subject", …)` exactly as `params_for_frames` does for a subject source, without calling `analyze_view` for the profile. `"auto"` is `params_for_frames` unchanged.
- Produces: `_source_json` gains `has_boxes: bool`, `scorer_now: str` (resolve_scorer of current columns) and passes `mount`, `play_format`, `court_corners`, `detected_scorer` through from the row.
- Detect handler, `/resegment` and CLI `segment` record `set_source_scorer` beside `set_source_detection` / `set_source_segment_threshold`.

- [ ] **Step 1: Write failing tests** — `test_resolve_scorer_table` (all 12 combinations); `test_not_set_source_segments_exactly_as_before` (a `mount=''` source over `tests/fixtures/ground_level_source01.jsonl` → intervals identical to `segment(frames, params_for_frames(frames))`); `test_ground_forces_subject_even_when_view_says_pair` (2026-10-12 fixture features, mount `ground` → `scorer == "subject"`); `test_fence_source_uses_model` (fixture files in a tmp src_dir, mount fence/singles, corners set → `scorer == "fence"`, intervals non-empty); `test_resegment_fence_without_boxes_is_409_with_sentence` (Review Focus 1); `test_detect_records_scorer`.
- [ ] **Step 2: Run, verify they fail.**
- [ ] **Step 3: Implement** `scorer.py` and route every listed caller through it; `/scores` returns the fence probabilities and the model threshold for fence sources so the slider opens on the right scale.
- [ ] **Step 4: Run the full suite.**
- [ ] **Step 5: Commit** — `feat(detect): one dispatcher picks fence, subject or auto per source`.

---

### Task 8: Setup accepts mount, format and corners

**Files:**
- Modify: `splitstep/setup.py::queue_setup`, `splitstep/api/routes.py` (`SetupBody` ~317, `api_setup` ~1308, new `GET /api/setup-defaults`), `splitstep/cli.py::cmd_setup` (`--mount`, `--format`, `--corners x,y;x,y;x,y;x,y`)
- Test: extend `tests/test_setup.py` and the setup route tests

**Interfaces:**
- Consumes: `set_source_setup` and `latest_setup_choices` (Task 1), `validate_corners` and `region_from_corners` (Task 4).
- Produces: `queue_setup(conn, source_id, rotation_deg, preset_id, *, mount: str = '', play_format: str = '', court_corners: list | None = None) -> str` — validation order: source, busy, rotation, mount/format membership, corners (`validate_corners`), then **fence + singles without corners → `ValueError("Fence + Singles needs the four court corners — draw them on the court lines.")`**, then preset. In fence mode with corners and no preset, the caller may pass the preset the wizard created from `region_from_corners`.
- Produces: `SetupBody` gains `mount: str = ''`, `play_format: str = ''`, `court_corners: list[list[float]] | None = None`; `GET /api/setup-defaults` → `latest_setup_choices`.

- [ ] **Step 1: Write failing tests** — `test_queue_setup_refuses_fence_singles_without_corners` (sentence as above, row untouched, no job); `test_queue_setup_refuses_crossed_corners` (Review Focus 2 at the route: 422/400 carrying `validate_corners`'s sentence); `test_queue_setup_stores_choices_and_enqueues`; `test_cli_setup_accepts_mount_format_corners`; `test_setup_defaults_route`.
- [ ] **Step 2: Run, verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite.**
- [ ] **Step 5: Commit** — `feat(setup): mount, format and court corners, validated once for HTTP and CLI`.

---

### Task 9: Frontend logic — `lib/mount.ts`

**Files:**
- Create: `web/src/lib/mount.ts`
- Modify: `web/src/lib/types.ts` (source fields from Task 7), `web/src/lib/api.ts` (`setup` gains mount/format/corners; `setupDefaults()`)
- Test: `web/tests/mount.test.ts`

**Interfaces:**
- Produces: `MOUNTS = [{value:'fence',label:'Fence'},{value:'ground',label:'Ground'},{value:'',label:'Not set'}]`, `FORMATS` likewise (Singles, Doubles, Drills, Not set).
- Produces: `scorerFor(mount, format): 'fence' | 'subject' | 'auto'` — the same table as `resolve_scorer`, pinned by a shared fixture `tests/fixtures/scorer_cases.json` read by both `tests/test_scorer.py` and this test (the `score_cases.json` pattern).
- Produces: `untunedNote(mount, format): string | null` — `"Not tuned for fence + doubles yet — using the general detector."` for fence + doubles/drills/not set; null otherwise.
- Produces: `needsCorners(mount, format): boolean` (fence → true).
- Produces: `detectionOwed(source): 'none' | 'resegment' | 'redetect'` — compares `scorer_now` with `detected_scorer` and fence corners; `'resegment'` when fence is wanted and `has_boxes`, `'redetect'` when fence is wanted without boxes; unknown `detected_scorer` (NULL) on a `''`/`''` source is `'none'` (the `sameRegion` rule: unknown is not a change).

- [ ] **Step 1: Write failing tests** for each export, including `scorer_cases.json` agreement and `detectionOwed` for: fence wanted + boxes → resegment; fence wanted, no boxes → redetect; old source, NULL detected_scorer, Not set → none.
- [ ] **Step 2: Run, verify they fail** — `cd web && npx vitest run tests/mount.test.ts`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npx vitest run` and `npm run check` → pass.
- [ ] **Step 5: Commit** — `feat(web): mount and format logic, mirrored from the server's scorer table`.

---

### Task 10: Frontend — wizard selectors, corners, Detection panel

**Files:**
- Modify: `web/src/routes/Setup.svelte`, `web/src/components/QuadEditor.svelte` (and `QuadCanvas.svelte` if the drag handles need corner labels), `web/src/components/DetectionPanel.svelte`
- Test: none new (components verified by hand per CLAUDE.md); `npm run check`, `npm run build`

**Interfaces:**
- Consumes: Task 9's `MOUNTS`, `FORMATS`, `needsCorners`, `untunedNote`, `detectionOwed`, `api.setupDefaults`, `api.setup`.

- [ ] **Step 1: Setup wizard** — two rows of buttons beside rotation (selected = `border-fg`, no accent), pre-selected from `setupDefaults()` and then from the source's own values when set; the Setup `$effect` that clears per-source state also clears these (route-effect-staleness rule). When `needsCorners`, the quad editor's copy reads "Drag the four handles onto the court's corners — doubles sidelines and both baselines", labels the handles in the fixed order, sends `court_corners`, and creates the play-region preset from the server's `region_from_corners` result (or computes nothing client-side: send corners and let the server derive). Show `untunedNote` under the selectors when non-null.
- [ ] **Step 2: Detection panel** — when `detectionOwed` is `'resegment'`, the primary action is "Re-segment with fence mode" (no GPU); when `'redetect'`, a priced re-detect exactly like the changed-quad case; show `untunedNote` when relevant. Never auto-run.
- [ ] **Step 3: Verify** — `npm run check`, `npx vitest run`, `npm run build`; then by hand against a scratch copy of the library (memory: scratch library): open 2026-10-12's setup, pick Fence + Singles, draw corners, confirm the stored row and that the panel offers a re-detect.
- [ ] **Step 4: Commit** — `feat(web): mount/format selectors, court corners and the fence re-segment action`.

---

### Task 11: Docs, full verification, and Steven's run

**Files:**
- Modify: `CLAUDE.md` (Pipeline/Detection: the third scorer, `boxes.jsonl`, truth files, `fence train` held-out vs `fence score` in-sample; that fence mode is fitted on one session), `docs/SMOKE.md` (what was exercised)

- [ ] **Step 1: Update CLAUDE.md and SMOKE.md** at the file's existing density.
- [ ] **Step 2: Full verification** — `-m pytest -q`, `ruff check splitstep tests`, `cd web && npm run check && npx vitest run && npm run build`. All green; paste counts.
- [ ] **Step 3: Hand to Steven** — he draws corners on 2026-10-12 (checking them against the fixture corners) and starts the re-detect; after it, `splitstep fence score ae5e11fc8cee4dd19e6c88bb8240428f` (in-sample, labelled so) and his own review in the app are Validation 2.
- [ ] **Step 4: Commit** — `docs: fence mode`.
