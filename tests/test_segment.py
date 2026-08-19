import itertools

import pytest

from bootleg.detect.features import FeatureFrame, Player
from bootleg.detect.segment import SegmentParams, score_series, segment

SAMPLE_MS = 200  # 5 fps


def frames(spec: str, *, start_ms: int = 0) -> list[FeatureFrame]:
    """Build a feature stream from a compact string.

    'A' = both players active (rally-like)
    '.' = idle, nobody moving
    'O' = one player only, moving (ball retrieval)
    Each character is one 200 ms sample.
    """
    out = []
    for i, ch in enumerate(spec):
        t = start_ms + i * SAMPLE_MS
        if ch == "A":
            out.append(FeatureFrame(
                t, 2, Player(0.5, 0.9, 0.30, 2.0), Player(0.5, 0.4, 0.10, 2.0),
                hits=1, hit_reg=0.9))
        elif ch == "O":
            out.append(FeatureFrame(
                t, 1, Player(0.5, 0.9, 0.30, 2.0), None, hits=0, hit_reg=0.0))
        else:
            out.append(FeatureFrame(t, 0, None, None, hits=0, hit_reg=0.0))
    return out


@pytest.fixture
def params():
    return SegmentParams()


def test_empty_input_yields_no_intervals(params):
    assert segment([], params) == []


def test_all_idle_yields_no_intervals(params):
    assert segment(frames("." * 100), params) == []


def test_one_player_moving_is_not_a_rally(params):
    assert segment(frames("." * 20 + "O" * 40 + "." * 20), params) == []


def test_sustained_activity_yields_one_interval(params):
    # 40 samples of activity = 8 s
    result = segment(frames("." * 25 + "A" * 40 + "." * 25), params)
    assert len(result) == 1


def test_padding_is_applied(params):
    result = segment(frames("." * 25 + "A" * 40 + "." * 25), params)
    raw_start = 25 * SAMPLE_MS
    raw_end = 65 * SAMPLE_MS
    assert result[0].start_ms == raw_start - int(params.pad_start_s * 1000)
    assert result[0].end_ms == raw_end + int(params.pad_end_s * 1000)


def test_padding_clamps_at_zero(params):
    result = segment(frames("A" * 40 + "." * 25), params)
    assert result[0].start_ms == 0


def test_short_gap_is_closed(params):
    # 5 idle samples = 1.0 s < close_gap_s of 1.5 s
    result = segment(frames("." * 20 + "A" * 30 + "." * 5 + "A" * 30 + "." * 20), params)
    assert len(result) == 1


def test_long_gap_splits(params):
    # 20 idle samples = 4.0 s > close_gap_s
    result = segment(frames("." * 20 + "A" * 30 + "." * 20 + "A" * 30 + "." * 20), params)
    assert len(result) == 2


def test_ace_length_segment_survives(params):
    """A 2 s point is the canonical ace. The old 3 s floor deleted these."""
    result = segment(frames("." * 25 + "A" * 10 + "." * 25), params)
    assert len(result) == 1, "a 2 s rally must not be discarded"


def test_blip_below_min_duration_is_dropped(params):
    # 5 samples = 1.0 s < min_duration_s of 1.5 s
    assert segment(frames("." * 25 + "A" * 5 + "." * 25), params) == []


def test_confidence_is_between_zero_and_one(params):
    result = segment(frames("." * 25 + "A" * 40 + "." * 25), params)
    assert 0.0 <= result[0].confidence <= 1.0


def test_intervals_are_ordered_and_disjoint(params):
    spec = ("." * 20 + "A" * 30) * 3 + "." * 20
    result = segment(frames(spec), params)
    assert len(result) == 3
    for a, b in itertools.pairwise(result):
        assert a.end_ms < b.start_ms


def test_lower_threshold_finds_more(params):
    stream = frames("." * 20 + "A" * 20 + "." * 4 + "A" * 20 + "." * 20)
    strict = segment(stream, SegmentParams(threshold=0.95))
    loose = segment(stream, SegmentParams(threshold=0.15))
    assert len(loose) >= len(strict)


def test_audio_only_does_not_create_a_rally(params):
    """Hits with no player activity — an adjacent court — must not segment.

    Documentation test only: at default weights, audio-only contribution is
    (w_hits + w_regularity) / weight_total = 1.1 / 3.3 = 0.333, already below
    the 0.45 threshold, so this passes whether or not the `if not both` guard
    in `_raw_score` exists. It records the intended behavior at default
    params but cannot detect a regression that deletes the guard — see
    `test_audio_only_does_not_create_a_rally_with_audio_heavy_weights` below
    for the test that actually protects it.
    """
    stream = [
        FeatureFrame(i * SAMPLE_MS, 0, None, None, hits=2, hit_reg=1.0)
        for i in range(60)
    ]
    assert segment(stream, params) == []


def test_audio_only_does_not_create_a_rally_with_audio_heavy_weights():
    """Mutation-effective version of the guard test above.

    Raised w_hits/w_regularity are what make this test capable of failing:
    with weight_total = 1.0 + 0.9 + 0.3 + 3.0 + 2.0 = 7.2, an audio-only
    frame scores (3.0 + 2.0) / 7.2 = 0.694 without the `if not both` guard in
    `_raw_score` — well past the 0.45 threshold — versus 0.0 with it. Delete
    the guard and this test fails; the default-weight test above does not.

    Audio-heavy weights are not a contrived edge case: they are exactly what
    a user reaches for once they confirm ball contact is audible on their
    footage and want the audio channel to carry more of the score. That is
    precisely when the guard becomes load-bearing — without it, ball impacts
    carrying from an adjacent public court would manufacture rallies on an
    empty court.
    """
    tuned = SegmentParams(w_hits=3.0, w_regularity=2.0)
    stream = [
        FeatureFrame(i * SAMPLE_MS, 0, None, None, hits=2, hit_reg=1.0)
        for i in range(60)
    ]
    assert segment(stream, tuned) == []


def test_score_series_length_matches_input(params):
    stream = frames("A" * 17)
    assert len(score_series(stream, params)) == 17
