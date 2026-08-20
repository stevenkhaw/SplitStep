import pytest

from bootleg.detect.features import FeatureFrame, Player
from bootleg.detect.viewpoint import analyze_view


def test_ground_fixture_has_the_expected_shape(ground_features):
    frames = ground_features
    assert len(frames) == 1200
    assert frames[0].t_ms == 200_000
    assert sum(1 for f in frames if f.near is not None) == 1103
    assert sum(1 for f in frames if f.near is not None and f.far is not None) == 735


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
    """far_foot=0.55 means every one of these 10 frames is a pair, so this
    exercises the MIN_PAIRS_FOR_CONFIDENCE branch, not MIN_FRAMES_FOR_CONFIDENCE:
    10 pairs is under the 20-pair floor, so their median separation is not
    worth trusting even though a near box was present in all 10 frames.
    Subject is the safe default regardless of which floor tripped -- it gates
    on your own player's size, so a misclassification cannot invent rallies
    out of adjacent-court people."""
    view = analyze_view(_stream(10, near_foot=0.90, far_foot=0.55))
    assert view.profile == "subject"
    assert view.low_confidence is True


def test_empty_input_does_not_raise():
    view = analyze_view([])
    assert view.profile == "subject"
    assert view.frames_measured == 0
    # 0 < MIN_FRAMES_FOR_CONFIDENCE, so an empty features file is exactly the
    # "too short to know either way" case, not a confident subject-mode
    # reading -- params_for_frames logs a warning off this flag.
    assert view.low_confidence is True


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


def test_a_short_but_unambiguous_pair_stream_is_confident():
    """40 frames is below MIN_FRAMES_FOR_CONFIDENCE (50), but all 40 carry
    both boxes at an unambiguous separation -- 40 clean paired samples, well
    over MIN_PAIRS_FOR_CONFIDENCE (20). A short clip with unambiguous
    geometry is not an unclassifiable one: gating confidence on near-box
    count instead of pair count read this as low-confidence subject, which
    is what silently sent an 8 s two-player CLI fixture through the wrong
    scoring model (see tests/test_cli.py's seeded_source)."""
    view = analyze_view(_stream(40, near_foot=0.90, far_foot=0.55))
    assert view.profile == "pair"
    assert view.low_confidence is False
