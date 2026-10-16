"""Fence features, model file and fault-folding segmentation.

Everything here runs on the real 2026-10-12 fixtures (16 minutes of boxes,
audio hits and court corners) -- never on synthetic players, which move
about 8x faster than real ones. The model is fitted in-test with plain
numpy: the committed model file is the training step's output, and a test
that loaded it would pin whatever that fit happened to be.
"""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from splitstep.detect.boxes import read_boxes
from splitstep.detect.court import Court
from splitstep.detect.features import read_features
from splitstep.detect.fence import (
    FEATURE_NAMES,
    FenceModel,
    fence_features,
    fence_probs,
    fence_segment,
)
from splitstep.detect.truth import load_truth

FIXTURES = Path(__file__).parent / "fixtures"
BOXES = FIXTURES / "boxes/2026-10-12_source01_0-16min.jsonl"
FEATURES = FIXTURES / "features/2026-10-12_source01_0-16min.jsonl"
CORNERS = FIXTURES / "truth/2026-10-12_source01.corners.json"
TRUTH = FIXTURES / "truth/2026-10-12_source01.tsv"


@pytest.fixture(scope="module")
def fixture():
    frames = read_boxes(BOXES)
    hits = [f.hits for f in read_features(FEATURES)]
    court = Court.from_json(CORNERS.read_text())
    return frames, hits, court


def _fit(x: np.ndarray, y: np.ndarray) -> FenceModel:
    # The spike's L2 logistic regression, by gradient descent.
    mu, sd = x.mean(0), x.std(0) + 1e-9
    z = (x - mu) / sd
    w = np.zeros(z.shape[1])
    b = 0.0
    for _ in range(1500):
        p = 1 / (1 + np.exp(-(z @ w + b)))
        w -= 0.1 * (z.T @ (p - y) / len(y) + w / len(y))
        b -= 0.1 * float((p - y).mean())
    return FenceModel(
        features=FEATURE_NAMES, mean=tuple(mu), std=tuple(sd), weights=tuple(w), bias=b,
        trained_on=("2026-10-12/01",),
    )


@pytest.fixture(scope="module")
def model(fixture):
    frames, hits, court = fixture
    x, _ = fence_features(frames, hits, court)
    t = np.array([t for t, _ in frames])
    y = np.zeros(len(t))
    for p in load_truth(TRUTH).points:
        y[(t >= p.start_ms) & (t < p.end_ms)] = 1.0
    return _fit(x, y)


def _synthetic_model(**kw) -> FenceModel:
    k = len(FEATURE_NAMES)
    return FenceModel(FEATURE_NAMES, (0.0,) * k, (1.0,) * k, (0.0,) * k, 0.0, **kw)


def _runs(spans_s: list[tuple[float, float]], total_s: float = 120.0):
    """200 ms frames with probability 1 inside each (start, end) span, 0 elsewhere."""
    t = np.arange(0, int(total_s * 1000), 200)
    p = np.zeros(len(t))
    for a, b in spans_s:
        p[(t >= a * 1000) & (t < b * 1000)] = 1.0
    return t, p


def test_features_finite_and_aligned(fixture):
    frames, hits, court = fixture
    x, names = fence_features(frames, hits, court)
    assert names == FEATURE_NAMES
    assert x.shape == (4800, len(names))
    assert np.isfinite(x).all()


def test_features_refuse_misaligned_hits(fixture):
    frames, hits, court = fixture
    with pytest.raises(ValueError, match="same detect run"):
        fence_features(frames, hits[:-1], court)


def test_features_without_audio_are_finite(fixture, model):
    # Review Focus 3: a source with no usable audio still cuts from vision.
    frames, _, court = fixture
    silent = [0] * len(frames)
    x, _ = fence_features(frames, silent, court)
    assert np.isfinite(x).all()
    probs = fence_probs(frames, silent, court, model)
    assert np.isfinite(probs).all()
    assert fence_segment([t for t, _ in frames], probs, model)


def test_empty_stretch_gives_no_interval(fixture, model):
    # Review Focus 4: a covered lens. 2:00-3:00 holds 42% play, so the audio
    # there is busy -- without the presence gate the model cut a clip here.
    frames, hits, court = fixture
    lo, hi = 120_000, 180_000
    blank = [(t, [] if lo <= t < hi else dets) for t, dets in frames]
    probs = fence_probs(blank, hits, court, model)
    assert np.isfinite(probs).all()
    intervals = fence_segment([t for t, _ in blank], probs, model)
    assert intervals, "the rest of the source should still cut"
    assert not [i for i in intervals if i.end_ms > lo and i.start_ms < hi]


def test_fold_merges_fault_into_next_point():
    t, p = _runs([(10, 14), (24, 32)])
    out = fence_segment(t, p, _synthetic_model())
    assert len(out) == 1
    # The fault's start (minus the start pad) survives; the point's end too.
    assert out[0].start_ms == pytest.approx(9_500, abs=600)
    assert out[0].end_ms == pytest.approx(32_700, abs=600)


def test_fold_leaves_long_clips_apart():
    t, p = _runs([(10, 19), (29, 37)])
    out = fence_segment(t, p, _synthetic_model())
    assert len(out) == 2


def test_fold_needs_the_next_clip_within_reach():
    # A short clip followed by nothing for 20 s stays on its own.
    t, p = _runs([(10, 14), (34, 42)])
    assert len(fence_segment(t, p, _synthetic_model())) == 2


def test_segment_drops_short_and_closes_gaps():
    # 1 s blip is dropped; a 0.6 s gap inside a rally is closed.
    t, p = _runs([(5, 6), (40, 50), (50.6, 60)])
    out = fence_segment(t, p, _synthetic_model(fold_max_s=0.0))
    assert len(out) == 1
    assert 39_000 < out[0].start_ms < 40_000 and 60_000 < out[0].end_ms < 61_500


def test_segment_threshold_override():
    t, p = _runs([(10, 20)])
    m = _synthetic_model()
    assert fence_segment(t, p * 0.5, m)
    assert not fence_segment(t, p * 0.5, m, threshold=0.6)


def test_segment_empty_input():
    assert fence_segment([], np.zeros(0), _synthetic_model()) == []


def test_model_round_trips(tmp_path, model):
    path = tmp_path / "fence_model.json"
    model.save(path)
    assert FenceModel.load(path) == model


def test_model_load_refuses_feature_mismatch(tmp_path, model):
    path = tmp_path / "fence_model.json"
    k = len(FEATURE_NAMES) - 1
    stale = replace(
        model, features=FEATURE_NAMES[:-1], mean=model.mean[:k], std=model.std[:k],
        weights=model.weights[:k],
    )
    stale.save(path)
    with pytest.raises(ValueError, match=f"missing {FEATURE_NAMES[-1]}.*re-fit"):
        FenceModel.load(path)


def test_predict_survives_zero_std():
    m = replace(_synthetic_model(), std=(0.0,) * len(FEATURE_NAMES),
                weights=(1.0,) * len(FEATURE_NAMES))
    p = m.predict(np.ones((3, len(FEATURE_NAMES))) * 1e6)
    assert np.isfinite(p).all() and (p <= 1).all()
