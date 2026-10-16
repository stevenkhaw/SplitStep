"""The one dispatcher every segmenting caller goes through.

Real fixtures only: the ground-level slice for "not set" and "ground", the
2026-10-12 fence clip-on for "fence" -- the latter is also the source whose
view analyze_view calls `pair`, which is what makes it the honest test of
Ground forcing `subject`.
"""

import json
import shutil
from pathlib import Path

import pytest

from splitstep.detect.features import read_features
from splitstep.detect.scorer import (
    FenceUnavailable,
    resolve_scorer,
    segment_source,
)
from splitstep.detect.segment import (
    SUBJECT_CLOSE_GAP_S,
    SUBJECT_THRESHOLD,
    params_for_frames,
    score_series,
    segment,
)
from splitstep.detect.viewpoint import analyze_view

FIXTURES = Path(__file__).parent / "fixtures"
GROUND = FIXTURES / "ground_level_source01.jsonl"
BOXES = FIXTURES / "boxes/2026-10-12_source01_0-16min.jsonl"
FENCE_FEATURES = FIXTURES / "features/2026-10-12_source01_0-16min.jsonl"
CORNERS = FIXTURES / "truth/2026-10-12_source01.corners.json"


def _source(mount="", play_format="", corners=None):
    return {"mount": mount, "play_format": play_format, "court_corners": corners}


def _src_dir(tmp_path, features, boxes=None) -> Path:
    shutil.copy(features, tmp_path / "features.jsonl")
    if boxes is not None:
        shutil.copy(boxes, tmp_path / "boxes.jsonl")
    return tmp_path


CASES = json.loads((FIXTURES / "scorer_cases.json").read_text())


def test_scorer_cases_cover_every_combination():
    # The TypeScript mirror reads the same file; a missing row would let the
    # two disagree on exactly the case nobody wrote down.
    combos = {(c["mount"], c["play_format"]) for c in CASES}
    assert combos == {(m, f) for m in ("", "fence", "ground")
                      for f in ("", "singles", "doubles", "drills")}
    assert len(CASES) == 12


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c['mount']}-{c['play_format']}")
def test_resolve_scorer_table(case):
    assert resolve_scorer(case["mount"], case["play_format"]) == case["scorer"]


def test_not_set_source_segments_exactly_as_before(tmp_path):
    frames = read_features(GROUND)
    params = params_for_frames(frames)
    got = segment_source(_source(), _src_dir(tmp_path, GROUND))
    assert got.intervals == segment(frames, params)
    assert got.threshold == params.threshold
    assert got.scores == score_series(frames, params)
    assert got.scorer == params.profile == "subject"
    assert got.step_ms == 200


def test_not_set_source_honours_an_explicit_threshold(tmp_path):
    frames = read_features(GROUND)
    params = params_for_frames(frames, threshold=0.15)
    got = segment_source(_source(), _src_dir(tmp_path, GROUND), threshold=0.15)
    assert got.intervals == segment(frames, params)
    assert got.threshold == 0.15


def test_fence_with_another_format_runs_auto(tmp_path):
    frames = read_features(FENCE_FEATURES)
    got = segment_source(_source("fence", "doubles"), _src_dir(tmp_path, FENCE_FEATURES))
    assert got.scorer == "pair"
    assert got.intervals == segment(frames, params_for_frames(frames))


def test_ground_forces_subject_even_when_view_says_pair(tmp_path):
    frames = read_features(FENCE_FEATURES)
    view = analyze_view(frames)
    assert view.profile == "pair"  # the guess Ground exists to override
    got = segment_source(_source("ground", "singles"), _src_dir(tmp_path, FENCE_FEATURES))
    assert got.scorer == "subject"
    assert got.threshold == SUBJECT_THRESHOLD
    # What params_for_frames builds for a subject source, minus the guess.
    from splitstep.detect.segment import SegmentParams
    params = SegmentParams(profile="subject", subject_min_h=view.subject_min_h,
                           threshold=SUBJECT_THRESHOLD, close_gap_s=SUBJECT_CLOSE_GAP_S)
    assert got.intervals == segment(frames, params)


def test_ground_on_a_subject_view_matches_auto(tmp_path):
    # Where analyze_view already says subject, forcing it changes nothing.
    frames = read_features(GROUND)
    got = segment_source(_source("ground", ""), _src_dir(tmp_path, GROUND))
    assert got.intervals == segment(frames, params_for_frames(frames))


def test_fence_source_uses_model(tmp_path):
    from splitstep import resources
    from splitstep.detect.fence import FenceModel

    model = FenceModel.load(resources.fence_model())
    got = segment_source(_source("fence", "singles", CORNERS.read_text()),
                         _src_dir(tmp_path, FENCE_FEATURES, BOXES))
    assert got.scorer == "fence"
    assert got.intervals
    assert got.threshold == model.threshold
    assert got.step_ms == 200
    assert len(got.scores) == len(read_features(FENCE_FEATURES))
    assert all(0.0 <= s <= 1.0 for s in got.scores)


def test_fence_threshold_override_changes_the_cut(tmp_path):
    src = _source("fence", "singles", CORNERS.read_text())
    d = _src_dir(tmp_path, FENCE_FEATURES, BOXES)
    low = segment_source(src, d, threshold=0.1)
    high = segment_source(src, d, threshold=0.6)
    assert low.threshold == 0.1 and high.threshold == 0.6
    assert low.intervals != high.intervals


def test_fence_without_boxes_says_rerun_detection(tmp_path):
    with pytest.raises(FenceUnavailable, match="re-run detection to use fence mode"):
        segment_source(_source("fence", "singles", CORNERS.read_text()),
                       _src_dir(tmp_path, FENCE_FEATURES))


def test_fence_without_corners_says_draw_them(tmp_path):
    with pytest.raises(FenceUnavailable, match="draw the court corners"):
        segment_source(_source("fence", "singles", None),
                       _src_dir(tmp_path, FENCE_FEATURES, BOXES))
