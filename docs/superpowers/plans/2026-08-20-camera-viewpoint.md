# Camera Viewpoint and Subject-Mode Segmentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Segment ground-level footage correctly by classifying each source's camera viewpoint from its features and scoring subject-mode sources on a size-gated presence test plus audio, while leaving the existing two-player model untouched.

**Architecture:** A new pure module `bootleg/detect/viewpoint.py` measures the median vertical separation between the two largest person boxes and returns a `ViewGeometry` (`pair` or `subject`, plus a derived box-height floor). `SegmentParams` gains a `profile` field; `_raw_score` branches on it. One helper, `params_for_frames()`, is the single place that turns a feature stream into params, so the detect handler, the CLI and the API cannot drift.

**Tech Stack:** Python 3.12 (conda env `bootleg`), pytest, ruff, Svelte 5, vitest.

## Global Constraints

- Python interpreter is not on the default PATH: invoke `~/miniconda3/envs/bootleg/bin/python`, `~/miniconda3/envs/bootleg/bin/pytest`, `~/miniconda3/envs/bootleg/bin/ruff`.
- `pytest` runs with `filterwarnings = ["error"]`. A new warning fails the suite.
- ruff line-length is 100.
- Comments explain **why**, not what. This codebase carries long rationale comments on non-obvious calls. Match that density.
- YOLO is never run in tests. Detector output is fixtured or mocked everywhere.
- Classifier cut: `foot_separation < 0.05` → `subject`, otherwise `pair`. Verbatim from spec §4.
- Subject-mode defaults: `threshold=0.25`, `close_gap_s=2.0`. `min_duration_s`, `pad_start_s`, `pad_end_s` unchanged. Verbatim from spec §5.
- `subject_min_h = 0.5 × median(near.h)`, floored at 0.02. Verbatim from spec §4.
- Do not change pair-mode scoring, weights, or defaults. Spec §6.
- Spec: `docs/superpowers/specs/2026-08-20-camera-viewpoint-design.md`.

---

## File Structure

| File | Responsibility |
|---|---|
| `bootleg/detect/viewpoint.py` | **new** — `ViewGeometry`, `analyze_view()`. Pure; imports only `features`. |
| `bootleg/detect/segment.py` | modified — `profile`/`subject_min_h` on `SegmentParams`, `_subject_score`, `params_for_frames()`. |
| `bootleg/jobs/handlers.py` | modified — detect handler uses `params_for_frames`. |
| `bootleg/cli.py` | modified — `--threshold` becomes optional, resolved per source. |
| `bootleg/api/routes.py` | modified — `threshold` optional on resegment and scores. |
| `tests/fixtures/ground_level_source01.jsonl` | **new** — 1200-frame slice of real footage. Golden fixture. |
| `tests/test_viewpoint.py` | **new** — classifier tests, synthetic + golden. |
| `tests/test_segment.py` | modified — subject-mode scoring and gate tests. |
| `web/src/lib/api.ts`, `web/src/lib/types.ts` | modified — threshold optional on `scores`. |
| `web/src/components/ResegmentPanel.svelte`, `TimelineMode.svelte` | modified — threshold comes from the API, not a constant. |

---

## Task 1: Golden fixture from real footage

The root cause of this whole spec was calibrating against synthetic signals. Every later task validates against this file.

**Files:**
- Create: `tests/fixtures/ground_level_source01.jsonl`
- Create: `tests/conftest.py` (or extend if it exists)
- Test: `tests/test_viewpoint.py`

**Interfaces:**
- Consumes: nothing.
- Produces: pytest fixture `ground_features` → `list[FeatureFrame]`, 1200 frames.

- [ ] **Step 1: Generate the fixture from the real source**

The library drive must be mounted. This is a one-time extraction — the output is committed and never regenerated.

```bash
~/miniconda3/envs/bootleg/bin/python -c "
from bootleg.detect.features import read_features, write_features
from pathlib import Path
src = Path('/Volumes/SanDisk_2TB/BootlegVision/sessions/2026-08-18/sources/01/features.jsonl')
out = Path('tests/fixtures/ground_level_source01.jsonl')
out.parent.mkdir(parents=True, exist_ok=True)
write_features(out, read_features(src)[1000:2200])
print(out, out.stat().st_size // 1024, 'KB')
"
```

Expected: `tests/fixtures/ground_level_source01.jsonl 152 KB`

- [ ] **Step 2: Confirm git will track it**

`.gitignore` has `*.jsonl` on line 11 and `!tests/fixtures/**/*.jsonl` on line 30.

```bash
git check-ignore -v tests/fixtures/ground_level_source01.jsonl; echo "exit=$?"
```

Expected: `exit=1` (not ignored).

- [ ] **Step 3: Add the pytest fixture**

Append to `tests/conftest.py`, creating the file if absent:

```python
from pathlib import Path

import pytest

from bootleg.detect.features import FeatureFrame, read_features

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def ground_features() -> list[FeatureFrame]:
    """A 4-minute slice of real ground-level footage (source 01, t=200-440 s).

    Committed as source, not generated: every tuning constant in the detector
    was previously fitted against synthetic streams that hand every player
    v=2.0 -- roughly 8x anything real -- and that is precisely what produced
    the one-hit-per-clip bug this module exists to fix. Assertions that matter
    are made against this file, not against hand-written frames.
    """
    return read_features(FIXTURES / "ground_level_source01.jsonl")
```

- [ ] **Step 4: Write the fixture's shape test**

Create `tests/test_viewpoint.py`:

```python
def test_ground_fixture_has_the_expected_shape(ground_features):
    frames = ground_features
    assert len(frames) == 1200
    assert frames[0].t_ms == 200_000
    assert sum(1 for f in frames if f.near is not None) == 1103
    assert sum(1 for f in frames if f.near is not None and f.far is not None) == 735
```

- [ ] **Step 5: Run it**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_viewpoint.py -q`
Expected: PASS, 1 test.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/ground_level_source01.jsonl tests/conftest.py tests/test_viewpoint.py
git commit -m "test: commit a real-footage golden fixture for the detector"
```

---

## Task 2: The viewpoint classifier

**Files:**
- Create: `bootleg/detect/viewpoint.py`
- Test: `tests/test_viewpoint.py`

**Interfaces:**
- Consumes: `ground_features` fixture from Task 1.
- Produces: `ViewGeometry(profile, foot_separation, subject_min_h, frames_measured, low_confidence)` and `analyze_view(frames: list[FeatureFrame]) -> ViewGeometry`. Task 3 consumes both.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_viewpoint.py`:

```python
from bootleg.detect.features import FeatureFrame, Player
from bootleg.detect.viewpoint import analyze_view

SAMPLE_MS = 200


def _stream(n: int, *, near_foot: float, far_foot: float | None,
            near_h: float = 0.30, far_h: float = 0.10) -> list[FeatureFrame]:
    out = []
    for i in range(n):
        far = None if far_foot is None else Player(0.5, far_foot, far_h, 0.2)
        out.append(FeatureFrame(
            i * SAMPLE_MS, 1 if far is None else 2,
            Player(0.5, near_foot, near_h, 0.2), far, hits=0, hit_reg=0.0))
    return out


def test_separated_players_classify_as_pair():
    """An elevated camera puts the far player up the frame, near the service
    line -- a foot separation of 0.15-0.35."""
    view = analyze_view(_stream(200, near_foot=0.90, far_foot=0.55))
    assert view.profile == "pair"
    assert view.low_confidence is False


def test_players_on_the_same_horizon_classify_as_subject():
    """A camera a foot off the ground compresses the far half of the court
    into a ~2% band, so both boxes share a foot line however far apart the
    players actually are."""
    view = analyze_view(_stream(200, near_foot=0.845, far_foot=0.840))
    assert view.profile == "subject"


def test_a_source_that_never_sees_a_far_player_is_subject():
    view = analyze_view(_stream(200, near_foot=0.90, far_foot=None))
    assert view.profile == "subject"
    assert view.low_confidence is False


def test_too_few_frames_is_subject_and_low_confidence():
    """Under 50 frames carrying a near box, the median is not worth trusting.
    Subject is the safe default -- it gates on your own player's size, so a
    misclassification cannot invent rallies out of adjacent-court people."""
    view = analyze_view(_stream(10, near_foot=0.90, far_foot=0.55))
    assert view.profile == "subject"
    assert view.low_confidence is True


def test_empty_input_does_not_raise():
    view = analyze_view([])
    assert view.profile == "subject"
    assert view.frames_measured == 0


def test_subject_min_h_is_half_the_median_near_height():
    view = analyze_view(_stream(200, near_foot=0.90, far_foot=None, near_h=0.40))
    assert view.subject_min_h == 0.2


def test_subject_min_h_has_a_floor():
    """A degenerate stream of zero-height boxes must not produce a floor of 0,
    which would open the gate on literally every detection."""
    view = analyze_view(_stream(200, near_foot=0.90, far_foot=None, near_h=0.0))
    assert view.subject_min_h == 0.02


def test_real_ground_footage_classifies_as_subject(ground_features):
    """The measurement that this whole design rests on."""
    view = analyze_view(ground_features)
    assert view.profile == "subject"
    assert view.foot_separation == pytest.approx(0.0054, abs=0.0005)
    assert view.subject_min_h == pytest.approx(0.1116, abs=0.0005)
    assert view.low_confidence is False
```

Add `import pytest` at the top of the file if it is not already there.

- [ ] **Step 2: Run to verify they fail**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_viewpoint.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bootleg.detect.viewpoint'`

- [ ] **Step 3: Write the module**

Create `bootleg/detect/viewpoint.py`:

```python
import statistics
from dataclasses import dataclass
from typing import Literal

from bootleg.detect.features import FeatureFrame

# Median |near.foot - far.foot| below which the two largest boxes are sharing
# a horizon line rather than standing at different depths. A camera propped a
# foot off the ground measures 0.006 (p90 0.010) on real footage; an elevated
# camera puts the far player near the service line, 0.15-0.35 away. 0.05 is
# the midpoint of the empty band between them -- 5x the observed ground-level
# p90, and 3x below the expected elevated minimum.
GROUND_FOOT_SEPARATION = 0.05

# Below this many frames carrying a near box, the median is noise. Subject is
# the safe default: it gates on your own player's box size, so guessing wrong
# cannot manufacture rallies out of people on the next court.
MIN_FRAMES_FOR_CONFIDENCE = 50

SUBJECT_H_FRACTION = 0.5
MIN_SUBJECT_H = 0.02

Profile = Literal["pair", "subject"]


@dataclass(frozen=True)
class ViewGeometry:
    profile: Profile
    foot_separation: float
    subject_min_h: float
    frames_measured: int
    low_confidence: bool


def analyze_view(frames: list[FeatureFrame]) -> ViewGeometry:
    """Classify a source's camera viewpoint from its own feature stream.

    Pure and cheap (~1 ms), so it is recomputed on every segment run rather
    than stored: no migration, and the re-segment slider stays instant.

    The discriminator is vertical separation, not box size. Size ratio also
    varies with camera height, but it varies with lens and court length too;
    where the feet land is a direct consequence of the camera's height above
    the surface and nothing else.
    """
    near_heights = [f.near.h for f in frames if f.near is not None]
    subject_min_h = max(
        SUBJECT_H_FRACTION * statistics.median(near_heights) if near_heights else 0.0,
        MIN_SUBJECT_H,
    )
    measured = len(near_heights)

    if measured < MIN_FRAMES_FOR_CONFIDENCE:
        return ViewGeometry("subject", 0.0, subject_min_h, measured, True)

    pairs = [(f.near, f.far) for f in frames
             if f.near is not None and f.far is not None]
    if not pairs:
        # A far player that is never seen at all is not a low-confidence
        # reading -- it is the clearest possible subject-mode signal.
        return ViewGeometry("subject", 0.0, subject_min_h, measured, False)

    separation = statistics.median(abs(n.foot - f.foot) for n, f in pairs)
    profile: Profile = "subject" if separation < GROUND_FOOT_SEPARATION else "pair"
    return ViewGeometry(profile, round(separation, 4), subject_min_h, measured, False)
```

- [ ] **Step 4: Run tests**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_viewpoint.py -q`
Expected: PASS, 9 tests.

- [ ] **Step 5: Lint**

Run: `~/miniconda3/envs/bootleg/bin/ruff check bootleg tests`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add bootleg/detect/viewpoint.py tests/test_viewpoint.py
git commit -m "feat(detect): classify camera viewpoint from feature geometry"
```

---

## Task 3: Subject-mode scoring

**Files:**
- Modify: `bootleg/detect/segment.py`
- Test: `tests/test_segment.py`

**Interfaces:**
- Consumes: `ViewGeometry`, `analyze_view` from Task 2.
- Produces: `SegmentParams(profile=..., subject_min_h=...)` and `params_for_frames(frames, *, threshold=None) -> SegmentParams`. Task 4 consumes `params_for_frames`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_segment.py`:

```python
# -- Subject mode ----------------------------------------------------------

SUBJECT = SegmentParams(profile="subject", subject_min_h=0.1116, threshold=0.25,
                        close_gap_s=2.0)


def _subject_frames(n: int, *, near_h: float = 0.23, hits_every: int = 0,
                    moving: bool = True) -> list[FeatureFrame]:
    """Ground-level frames: one big near box, plus a horizon-sized stranger."""
    out = []
    for i in range(n):
        cx = 0.5 + (i % 2) * (0.010 if moving else 0.0)
        out.append(FeatureFrame(
            i * SAMPLE_MS, 2,
            Player(cx, 0.845, near_h, 0.25 if moving else 0.0),
            Player(0.7, 0.840, 0.055, 0.1),     # stranger on the next court
            hits=1 if (hits_every and i % hits_every == 0) else 0,
            hit_reg=0.25))
    return out


def test_subject_mode_scores_a_rally_above_threshold():
    scores = score_series(_subject_frames(40, hits_every=7), SUBJECT)
    assert max(scores) >= SUBJECT.threshold


def test_subject_mode_gate_closes_when_only_strangers_are_present():
    """The adjacent-court guard. Horizon-sized boxes plus loud, regular audio
    must score exactly 0 -- not merely below threshold -- because the gate
    never opens. This is the mutation-effective form: delete the gate and the
    audio terms alone carry these frames straight over any threshold.
    """
    stream = [
        FeatureFrame(i * SAMPLE_MS, 3,
                     Player(0.7, 0.840, 0.055, 0.4),   # too small to be yours
                     Player(0.3, 0.841, 0.050, 0.4),
                     hits=2, hit_reg=1.0)
        for i in range(60)
    ]
    assert max(score_series(stream, SUBJECT)) == 0.0
    assert segment(stream, SUBJECT) == []


def test_subject_mode_ignores_the_far_box_entirely():
    """Speed comes from near.v alone. A far box reporting v=0 -- what every
    re-acquisition after a gap reports, 22% of far boxes on real footage --
    must not drag the score down, which is exactly what min(near.v, far.v)
    did before."""
    with_far = score_series(_subject_frames(40, hits_every=7), SUBJECT)
    without = [FeatureFrame(f.t_ms, 1, f.near, None, f.hits, f.hit_reg)
               for f in _subject_frames(40, hits_every=7)]
    assert score_series(without, SUBJECT) == with_far


def test_subject_mode_presence_alone_is_not_enough():
    """Standing on court between points: gate open, but nothing moving and no
    audio. As an additive term presence was worth 0.303 of the score on 83% of
    real frames; as a gate it is worth nothing."""
    idle = _subject_frames(60, hits_every=0, moving=False)
    assert max(score_series(idle, SUBJECT)) < SUBJECT.threshold
    assert segment(idle, SUBJECT) == []


def test_params_for_frames_picks_subject_on_real_footage(ground_features):
    params = params_for_frames(ground_features)
    assert params.profile == "subject"
    assert params.threshold == 0.25
    assert params.close_gap_s == 2.0
    assert params.subject_min_h == pytest.approx(0.1116, abs=0.0005)


def test_params_for_frames_picks_pair_on_separated_players():
    params = params_for_frames(frames("A" * 200))
    assert params.profile == "pair"
    assert params.threshold == 0.45


def test_params_for_frames_honours_an_explicit_threshold(ground_features):
    """The re-segment slider overrides the profile default but not the profile."""
    params = params_for_frames(ground_features, threshold=0.4)
    assert params.threshold == 0.4
    assert params.profile == "subject"


def test_real_ground_footage_segments_into_rallies(ground_features):
    """End to end on the golden fixture. The pair model produced clips with a
    median of 3.9 s against a true median of 8.0 s; subject mode should land
    near the truth."""
    intervals = segment(ground_features, params_for_frames(ground_features))
    durations = sorted((iv.end_ms - iv.start_ms) / 1000 for iv in intervals)
    assert len(intervals) == 16
    assert statistics.median(durations) == pytest.approx(7.0, abs=0.5)
```

Add `import statistics` to the top of `tests/test_segment.py` if absent, and extend the
existing segment import to `from bootleg.detect.segment import (SegmentParams,
params_for_frames, score_series, segment)`. Do not add a mid-file import — ruff
flags E402 and the suite treats warnings as errors.

- [ ] **Step 2: Run to verify they fail**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_segment.py -q -k "subject or params_for_frames"`
Expected: FAIL — `ImportError: cannot import name 'params_for_frames'`

- [ ] **Step 3: Add the profile fields to `SegmentParams`**

In `bootleg/detect/segment.py`, add the import at the top:

```python
from bootleg.detect.viewpoint import Profile, ViewGeometry, analyze_view
```

Add these two fields to `SegmentParams`, after `pad_end_s`:

```python
    # Which scoring shape to use. "pair" is the original two-player model.
    # "subject" is for a camera low enough that the far half of the court
    # collapses onto the horizon, where the second-largest box is not the
    # opponent but whoever is on the next court -- see
    # docs/superpowers/specs/2026-08-20-camera-viewpoint-design.md.
    profile: Profile = "pair"
    # Minimum box height that counts as your player, in subject mode. Derived
    # per source by analyze_view; 0.0 here because pair mode never reads it.
    subject_min_h: float = 0.0
```

Replace the `weight_total` property:

```python
    @property
    def weight_total(self) -> float:
        # Subject mode gates on presence instead of scoring it, so w_both is
        # not part of the sum. This is why the two profiles' thresholds are
        # not comparable numbers: 0.25 subject, 0.45 pair.
        base = self.w_speed + self.w_lateral + self.w_hits + self.w_regularity
        return base if self.profile == "subject" else base + self.w_both
```

- [ ] **Step 4: Add the subject scorer and branch `_raw_score`**

Add above `_raw_score`:

```python
def _subject_score(f: FeatureFrame, p: SegmentParams, lateral: float) -> float:
    """Score a frame where only your own player is reliably visible.

    Presence is a gate, not a term. Your player is on court 83% of the time on
    real footage -- including between points, walking to the baseline, picking
    up balls -- so an additive presence term handed out a third of the score
    for free on nearly every frame. As a gate it earns nothing and the audio
    and motion terms have to carry the frame on their own.

    The gate also replaces the `if not both` guard that keeps audio from
    segmenting an empty court: a box has to be your player's size before any
    impact counts, and people on adjacent courts sit at the horizon at roughly
    a quarter of that height.
    """
    if f.near is None or f.near.h < p.subject_min_h:
        return 0.0

    speed = _clamp01(f.near.v / MAX_SPEED)
    hits = _clamp01(f.hits / MAX_HIT_RATE)
    reg = _clamp01(f.hit_reg)

    score = (
        p.w_speed * speed
        + p.w_lateral * lateral
        + p.w_hits * hits
        + p.w_regularity * reg
    )
    return _clamp01(score / p.weight_total)
```

Add this as the first two lines of the body of `_raw_score`:

```python
    if p.profile == "subject":
        return _subject_score(f, p, lateral)
```

- [ ] **Step 5: Add `params_for_frames`**

Add at the end of `bootleg/detect/segment.py`:

```python
# Subject mode's own defaults. Fitted against audio-impact clusters on the one
# real ground-level source (59 clusters, median 8.0 s, 59% coverage); these
# give 61 intervals, median 7.6 s, 63% coverage. That fit is partly circular --
# audio drives both the score and the labels -- so treat them as provisional
# until the visual spot-check in the plan's validation task is done.
SUBJECT_THRESHOLD = 0.25
SUBJECT_CLOSE_GAP_S = 2.0


def params_for_frames(
    frames: list[FeatureFrame], *, threshold: float | None = None
) -> SegmentParams:
    """Build the right SegmentParams for a source, from the source itself.

    Every caller that segments -- the detect handler, the CLI, both API routes
    -- goes through here, for the same reason setup.py::queue_setup exists:
    HTTP and terminal must not be able to drift on which model a source gets.

    `threshold=None` means "use the profile's default", which is what the UI
    wants on first load; the two profiles' thresholds are on different scales
    and hardcoding either one in a caller is a bug.
    """
    view: ViewGeometry = analyze_view(frames)
    if view.profile == "subject":
        params = SegmentParams(
            profile="subject",
            subject_min_h=view.subject_min_h,
            threshold=SUBJECT_THRESHOLD,
            close_gap_s=SUBJECT_CLOSE_GAP_S,
        )
    else:
        params = SegmentParams()
    if threshold is not None:
        params = replace(params, threshold=threshold)
    return params
```

Add `replace` to the dataclasses import at the top:

```python
from dataclasses import dataclass, replace
```

- [ ] **Step 6: Run the full suite**

Run: `~/miniconda3/envs/bootleg/bin/pytest -q`
Expected: PASS. Existing pair-mode tests must be untouched — their fixtures use `near_foot=0.9, far_foot=0.4`, a separation of 0.5, so they classify as `pair`.

- [ ] **Step 7: Lint**

Run: `~/miniconda3/envs/bootleg/bin/ruff check bootleg tests`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add bootleg/detect/segment.py tests/test_segment.py
git commit -m "feat(detect): add subject-mode scoring gated on your player's box size"
```

---

## Task 4: Wire the three call sites

**Files:**
- Modify: `bootleg/jobs/handlers.py:304`
- Modify: `bootleg/cli.py:210`, `bootleg/cli.py:312`
- Modify: `bootleg/api/routes.py:68`, `:187`, `:205`, `:217`
- Test: `tests/test_handlers.py`, `tests/test_cli.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: `params_for_frames` from Task 3.
- Produces: `/api/sources/{id}/scores` and `/resegment` accept `threshold` as optional; `scores` returns the resolved threshold.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_handlers.py`:

```python
def test_detect_uses_subject_mode_on_ground_level_features(
    library, conn, ground_features
):
    """The detect handler must pick the profile from the features, not assume
    pair. Wired wrong, a ground-level source silently gets the two-player
    model and every clip comes out one hit long again.
    """
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-18T19:00:00Z", duration_ms=240_000,
        width=1920, height=1080, fps=30.0, original_name="IMG_2373.MOV",
    )
    write_features(library.source_dir(session_id, idx) / "features.jsonl",
                   ground_features)
    HANDLERS["detect"](library, {"source_id": source_id, "reuse_features": True})

    rallies = list_rallies(conn, session_id)
    assert len(rallies) == 16
```

Match the imports already used at the top of `tests/test_handlers.py`.

- [ ] **Step 2: Run to verify it fails**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_handlers.py -q -k subject_mode`
Expected: FAIL — the count will not be 16, because `SegmentParams()` is pair mode.

- [ ] **Step 3: Update the detect handler**

In `bootleg/jobs/handlers.py`, replace line 304:

```python
    intervals = segment(frames, SegmentParams())
```

with:

```python
    intervals = segment(frames, params_for_frames(frames))
```

Update the import in that file from `SegmentParams` to `params_for_frames` (keep `segment`). Remove `SegmentParams` from the import if nothing else in the file uses it — check with `grep -n SegmentParams bootleg/jobs/handlers.py`.

- [ ] **Step 4: Update the CLI**

In `bootleg/cli.py`, replace line 210:

```python
    params = SegmentParams(threshold=args.threshold)
```

with:

```python
    params = params_for_frames(frames, threshold=args.threshold)
```

Replace the summary print so it reports the threshold actually used, not the argument:

```python
        print(f"\n{len(intervals)} rallies at threshold {params.threshold}")
```

Change the argument default at line 312 so "not given" is distinguishable from a value:

```python
    p.add_argument("--threshold", type=float, default=None,
                   help="override the profile's default threshold")
```

- [ ] **Step 5: Update the API**

In `bootleg/api/routes.py`, change `ResegmentBody` (keep the existing NaN comment above it):

```python
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
```

Replace line 187:

```python
    frames = read_features(path)
    intervals = segment(frames, params_for_frames(frames, threshold=body.threshold))
```

Change the scores route signature at line 205:

```python
def api_scores(source_id: str, request: Request,
               threshold: float | None = Query(default=None, ge=0.0, le=1.0)):
```

and its body at line 217:

```python
    params = params_for_frames(frames, threshold=threshold)
```

and the response, so the UI can read the resolved default back:

```python
        "threshold": params.threshold,
```

- [ ] **Step 6: Run the full suite**

Run: `~/miniconda3/envs/bootleg/bin/pytest -q`
Expected: PASS. `tests/test_cli.py` already reads its expected threshold from `SegmentParams().threshold`, and its fixture classifies as pair, so it still reads 0.45.

- [ ] **Step 7: Lint**

Run: `~/miniconda3/envs/bootleg/bin/ruff check bootleg tests`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add bootleg/jobs/handlers.py bootleg/cli.py bootleg/api/routes.py tests/
git commit -m "feat(detect): route every segment call through params_for_frames"
```

---

## Task 5: Validation gate — check the clips by eye

Spec §8. The 0.25 threshold was fitted against audio clusters while audio drives the score. **Do not skip this and do not defer it to the end.** Every number downstream rests on it.

**Files:**
- Create: `docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md` (findings)
- Possibly modify: `bootleg/detect/segment.py` (`SUBJECT_THRESHOLD`)

**Interfaces:**
- Consumes: the wired pipeline from Task 4.
- Produces: a confirmed or corrected `SUBJECT_THRESHOLD`.

- [ ] **Step 1: Re-segment the real source**

```bash
bootleg --library /Volumes/SanDisk_2TB/BootlegVision segment 0926ad87101b40dc9cd63a915858147c --dry-run
```

Expected: roughly 61 rallies, median duration near 7.6 s. Record the actual output.

- [ ] **Step 2: Cut six clips spanning the range**

Pick the 1st, 10th, 20th, 35th, 50th and last interval from that output. For each, with `START` and `DURATION` in seconds:

```bash
ffmpeg -v error -ss START -i /Volumes/SanDisk_2TB/BootlegVision/sessions/2026-08-18/sources/01/proxy.mp4 -t DURATION -c copy /tmp/clip_N.mp4
```

- [ ] **Step 3: Watch each clip and record the verdict**

For each clip write down: does it start within ~1 s of the first strike? Does it end within ~1 s of the last? Does it contain one rally or several? Is it a rally at all?

- [ ] **Step 4: Write up findings**

Create `docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md` with a row per clip: interval index, start/end, verdict, and any boundary error in seconds.

- [ ] **Step 5: Adjust the threshold if the verdict demands it**

If clips systematically start or end late, the threshold is too high; if they merge separate points, it is too low or `close_gap_s` is too large. Change `SUBJECT_THRESHOLD` in `bootleg/detect/segment.py`, update the expected counts in `test_real_ground_footage_segments_into_rallies` and `test_detect_uses_subject_mode_on_ground_level_features`, and re-run:

```bash
~/miniconda3/envs/bootleg/bin/pytest -q
```

If no adjustment is needed, say so explicitly in the findings document.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md bootleg/detect/segment.py tests/
git commit -m "docs: record visual validation of subject-mode rally boundaries"
```

---

## Task 6: The UI reads the threshold from the API

Two profiles put the threshold on two different scales, so a hardcoded slider default is now wrong for half of all sources.

**Files:**
- Modify: `web/src/lib/api.ts:31-34`
- Modify: `web/src/components/ResegmentPanel.svelte:18`, `:25`
- Modify: `web/src/components/TimelineMode.svelte:31`, `:39`
- Test: `web/tests/resegment-panel.test.ts`

**Interfaces:**
- Consumes: `/scores` returning the resolved `threshold` (Task 4).
- Produces: no new exports.

- [ ] **Step 1: Write the failing test**

In `web/tests/resegment-panel.test.ts`, change the mock so the API reports a subject-mode threshold, and assert the panel adopts it:

```ts
mockApi.scores.mockResolvedValue({ step_ms: 200, threshold: 0.25, scores: [0.1, 0.9] })
```

```ts
it('adopts the threshold the API reports instead of a hardcoded default', async () => {
  render(ResegmentPanel, { props: baseProps })
  await waitFor(() => expect(mockApi.scores).toHaveBeenCalled())
  await waitFor(() => expect(screen.getByRole('slider')).toHaveValue('0.25'))
})
```

Read `web/tests/resegment-panel.test.ts` first and reuse its existing render helper,
props object and query style. The selector above is illustrative — use whichever query
the file already uses to reach the threshold slider, since the component's markup is not
reproduced in this plan.

- [ ] **Step 2: Run to verify it fails**

Run (from `web/`): `npx vitest run tests/resegment-panel.test.ts`
Expected: FAIL — the slider still reads 0.45.

- [ ] **Step 3: Make the threshold optional in the client**

In `web/src/lib/api.ts`:

```ts
  scores: (sourceId: string, threshold?: number) =>
    req<ScoreSeries>(`/api/sources/${sourceId}/scores`
      + (threshold === undefined ? '' : `?threshold=${threshold}`)),
```

- [ ] **Step 4: Drop the constant in both components**

In `ResegmentPanel.svelte` and `TimelineMode.svelte`, delete the `DEFAULT_THRESHOLD` constant and its comment, and start the state as null:

```svelte
  // Null until the first /scores response supplies it. The two camera
  // profiles put the threshold on different scales (0.25 subject, 0.45 pair),
  // so a constant here is wrong for half of all sources -- the API resolves
  // it per source and this is where that answer lands.
  let threshold = $state<number | null>(null)
```

At the first `scores` call, pass no threshold and adopt what comes back; on later calls pass the current value. Disable the slider while `threshold === null`.

- [ ] **Step 5: Run the frontend checks**

Run (from `web/`): `npx vitest run`
Expected: PASS, all files.

Run (from `web/`): `npm run check`
Expected: `0 ERRORS 0 WARNINGS`

- [ ] **Step 6: Commit**

```bash
git add web/src web/tests
git commit -m "fix(web): take the segmentation threshold from the API, not a constant"
```

---

## Task 7: Documentation

**Files:**
- Modify: `HANDOFF.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update `HANDOFF.md`**

Replace the "Known weakness: far-player dropout" section — its premise is wrong, the absence was never dropout — with a description of the two profiles, the classifier cut, and the subject-mode defaults. Update the scoring formula block to show both shapes. Keep the failure-mode table and add: *"Ground-level source classified as pair"* → check `analyze_view` output; a wrong court quad that admits adjacent courts will inflate the foot separation.

- [ ] **Step 2: Update `CLAUDE.md`**

In the "Detection is deliberately two-stage" section, add a paragraph: scoring has two profiles, chosen automatically by `detect/viewpoint.py` from the feature stream; `segment()` is still pure and still takes params; `params_for_frames()` is the only sanctioned way to build them.

In "Conventions that matter", add: *"Tuning constants are validated against `tests/fixtures/ground_level_source01.jsonl`, a real slice. Synthetic fixtures hand every player `v=2.0`, ~8x reality — do not calibrate against them."*

- [ ] **Step 3: Verify everything still passes**

```bash
~/miniconda3/envs/bootleg/bin/pytest -q && ~/miniconda3/envs/bootleg/bin/ruff check bootleg tests
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add HANDOFF.md CLAUDE.md
git commit -m "docs: describe the two camera profiles and the real-footage fixture"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §3 architecture, `viewpoint.py`, pure, recomputed | 2 |
| §3 one shared helper for three call sites | 3 (helper), 4 (wiring) |
| §4 classifier, cut 0.05, direction | 2 |
| §4 `subject_min_h` derived, floored | 2 |
| §4 degenerate cases | 2 |
| §5 gate scoring, `near.v` alone | 3 |
| §5 subject defaults 0.25 / 2.0 | 3 |
| §5 threshold scale, UI reads from API | 4 (API), 6 (UI) |
| §6 pair mode untouched | 3 step 6 asserts existing tests unchanged |
| §7 fit numbers | 3, 5 |
| §8 classifier tests, gate tests, golden fixture, existing tests pass, non-circular check | 1, 2, 3, 5 |
| §9 out of scope: no audio changes, no DB storage | not implemented anywhere — correct |

**Type consistency:** `analyze_view`, `ViewGeometry`, `Profile`, `params_for_frames`, `SUBJECT_THRESHOLD`, `SUBJECT_CLOSE_GAP_S`, `subject_min_h`, `foot_separation`, `low_confidence`, `frames_measured` are spelled identically in Tasks 2, 3, 4 and 5.

**Note on Task 5:** it is a manual validation gate, so its "test" is a written findings document rather than an assertion. That is deliberate — the whole point is a check the automated suite structurally cannot make.
