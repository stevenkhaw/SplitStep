import itertools
import logging
import statistics

import pytest

from splitstep.detect.features import FeatureFrame, Player
from splitstep.detect.segment import (
    SegmentParams,
    params_for_frames,
    score_series,
    segment,
)

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


# -- Finding 8: lateral term measures motion, not position ------------------
#
# Isolate w_lateral as the only nonzero weight and disable smoothing, so
# score_series(...)[1] reads back the raw lateral fraction of the second
# frame's displacement directly through the public API.

LATERAL_ONLY = SegmentParams(
    w_both=0.0, w_speed=0.0, w_lateral=1.0, w_hits=0.0, w_regularity=0.0,
    w_outside=0.0, smooth_window_s=0.0,
)


def _lateral_pair(prev_near: Player, near: Player) -> list[FeatureFrame]:
    far = Player(0.5, 0.4, 0.10, 2.0)  # present so `both` is true; value unused
    return [
        FeatureFrame(0, 2, prev_near, far, hits=0, hit_reg=0.0),
        FeatureFrame(SAMPLE_MS, 2, near, far, hits=0, hit_reg=0.0),
    ]


def test_lateral_term_scores_pure_sideways_motion_high():
    """Sliding sideways with the foot position unchanged is fully lateral."""
    stream = _lateral_pair(Player(0.3, 0.5, 0.30, 2.0), Player(0.5, 0.5, 0.30, 2.0))
    assert score_series(stream, LATERAL_ONLY)[1] == 1.0


def test_lateral_term_scores_pure_longitudinal_motion_zero_even_off_center():
    """Moving straight toward the camera off to one side (retrieving a ball
    near the fence) must score 0. The old position-based formula scored this
    0.8 (abs(0.9 - 0.5) * 2) purely from where the player stood, ignoring
    that the motion itself was directly toward the camera -- this is the
    exact inversion Finding 8 describes, pinned so it cannot come back.
    """
    stream = _lateral_pair(Player(0.9, 0.5, 0.30, 2.0), Player(0.9, 0.7, 0.30, 2.0))
    assert score_series(stream, LATERAL_ONLY)[1] == 0.0


def test_lateral_term_scores_a_stationary_player_zero_even_off_center():
    """A player standing still at the sideline: the measured consequence
    from Finding 8 itself. The old formula scored this 0.9
    (abs(0.95 - 0.5) * 2) despite zero motion -- position, not motion. This
    test would fail if the position-based formula were reinstated.
    """
    stationary = Player(0.95, 0.9, 0.30, 2.0)
    stream = _lateral_pair(stationary, stationary)
    assert score_series(stream, LATERAL_ONLY)[1] == 0.0


def test_lateral_term_is_zero_on_the_first_frame_with_no_previous_position():
    far = Player(0.5, 0.4, 0.10, 2.0)
    stream = [FeatureFrame(0, 2, Player(0.9, 0.5, 0.30, 2.0), far, hits=0, hit_reg=0.0)]
    assert score_series(stream, LATERAL_ONLY)[0] == 0.0


# -- Real-footage calibration ----------------------------------------------
#
# Everything above this line is synthetic: the `frames()` helper hands every
# player v=2.0, which is 4x the fastest thing ever measured on real footage.
# The constants below are the ones measured on the first real source
# (sessions/2026-08-18/sources/01, 19.5 min, 5869 sampled frames), and they
# are what the defaults are now calibrated against. See
# test_rally_frame_without_a_recent_impact_clears_the_threshold for the
# regression these pin.

REAL_NEAR_V = 0.25   # p75 of measured near.v, body-lengths/sec
REAL_FAR_V = 0.15    # far player, correspondingly slower in the same units
REAL_HIT_REG = 0.25  # median hit_reg during frames with both players visible
REAL_DX = 0.010      # per-sample lateral displacement, normalized
REAL_DY = 0.003      # per-sample longitudinal displacement -> lateral ~= 0.77


def rally_frames(n: int, *, hit_every: int = 0, start_ms: int = 0,
                 gap_at: range | None = None) -> list[FeatureFrame]:
    """A rally at measured real-footage speeds, not synthetic ones.

    `hit_every` places one audio impact every N samples; 0 means silent.
    `gap_at` blanks the far player over those indices, reproducing the
    YOLO far-player dropout that occurs on 38% of real frames.
    """
    out = []
    for i in range(n):
        cx = 0.5 + (i % 2) * REAL_DX
        foot = 0.9 + (i % 2) * REAL_DY
        far = None if (gap_at is not None and i in gap_at) else \
            Player(0.5, 0.4, 0.10, REAL_FAR_V)
        out.append(FeatureFrame(
            start_ms + i * SAMPLE_MS,
            1 if far is None else 2,
            Player(cx, foot, 0.30, REAL_NEAR_V),
            far,
            hits=1 if (hit_every and i % hit_every == 0) else 0,
            hit_reg=REAL_HIT_REG,
        ))
    return out


def test_rally_frame_without_a_recent_impact_clears_the_threshold(params):
    """The bug that made every clip one hit long.

    Between two ball impacts there is no audio in the trailing 1 s window,
    so `hits` and `hit_reg` cannot carry the frame -- only `both`, `speed`
    and `lateral` can. With MAX_SPEED at its old synthetic 4.0, real
    body-length speeds (median 0.04 for min(near, far)) made the speed term
    contribute 0.009 out of a possible 0.27, leaving a plainly rallying
    frame at 0.408 against a 0.45 threshold. Every frame between impacts
    therefore fell below threshold and each clip collapsed to the ~1 s hit
    window plus padding.
    """
    scores = score_series(rally_frames(40), params)
    assert min(scores) >= params.threshold, (
        f"a rally frame with no recent impact scored {min(scores):.3f}, "
        f"below the {params.threshold} threshold"
    )


def test_rally_is_not_split_into_one_clip_per_impact(params):
    """A 12 s rally with impacts every 1.4 s is one clip, not eight."""
    result = segment(rally_frames(60, hit_every=7), params)
    assert len(result) == 1
    assert result[0].end_ms - result[0].start_ms >= 12_000


def test_far_player_dropout_does_not_split_a_rally(params):
    """The far player is occluded for 1.8 s mid-rally -- one clip, not two.

    `both` going false zeroes the score outright (and fires w_outside), so a
    frame missing the far player reads exactly like the end of a rally.

    Note this test is about the *pair* camera model. The 38% far-player
    absence measured on the one real source is NOT occlusion -- that camera
    sits a foot off the ground and its "far player" was people on adjacent
    courts. See docs/superpowers/specs/2026-08-20-camera-viewpoint-design.md.
    """
    stream = rally_frames(60, hit_every=7, gap_at=range(25, 34))
    assert len(segment(stream, params)) == 1


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


def test_subject_mode_gate_is_inclusive_at_the_boundary():
    """near.h exactly equal to subject_min_h must still open the gate.

    The boundary is what separates "your player" from an adjacent-court
    figure, so which side of it is inclusive (`>=`, not `>`) is a real
    decision and not an accident -- pin it directly instead of only testing
    values comfortably on one side.
    """
    stream = _subject_frames(40, hits_every=7, near_h=SUBJECT.subject_min_h)
    assert max(score_series(stream, SUBJECT)) > 0.0


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
    # Not asserting close_gap_s here: both profiles currently default to
    # 2.0, so this would pass even if SUBJECT_CLOSE_GAP_S were dropped from
    # the subject branch entirely. threshold is the field that actually
    # distinguishes the two profiles' params.
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


def test_params_for_frames_warns_on_low_confidence_classification(caplog):
    """Fewer than 50 frames with a near box means the profile guess is a
    coin flip, not a measurement -- see analyze_view's MIN_FRAMES_FOR_CONFIDENCE.
    That has to reach a human somehow, since the caller (detect handler, CLI,
    API) has no other signal that the source might need a look, e.g. a wrong
    court quad silently starving it of near-player boxes."""
    with caplog.at_level(logging.WARNING, logger="splitstep.detect.segment"):
        params_for_frames(frames("O" * 10))
    assert any("low-confidence" in r.message for r in caplog.records)


def test_params_for_frames_warns_on_pair_scarce_low_confidence(caplog):
    """low_confidence has a second, independent cause: pairs present but too
    few to trust their median (analyze_view's MIN_PAIRS_FOR_CONFIDENCE), which
    can fire with frames_measured well past MIN_FRAMES_FOR_CONFIDENCE -- the
    branch the frames-scarce test above does not exercise. 100 'O' frames
    plus 15 'A' frames put frames_measured at 115 (comfortably over the
    50-frame floor) but pairs_measured at 15 (under the 20-pair floor), so a
    warning that only ever named frames_measured would misdescribe this case
    as near-box scarcity when the real cause is pair scarcity."""
    with caplog.at_level(logging.WARNING, logger="splitstep.detect.segment"):
        params_for_frames(frames("O" * 100 + "A" * 15))
    assert any("low-confidence" in r.message for r in caplog.records)
    assert any("15 of those were paired" in r.message for r in caplog.records)


def test_params_for_frames_does_not_warn_on_a_confident_classification(caplog):
    with caplog.at_level(logging.WARNING, logger="splitstep.detect.segment"):
        params_for_frames(frames("A" * 200))
    assert caplog.records == []


def test_real_ground_footage_segments_into_rallies(ground_features):
    """End to end on the golden fixture. Regression pin, not a correctness
    check: this asserts subject mode keeps producing 16 intervals at a 7.0 s
    median on this fixture, distinct from the pair model's wrong 3.9 s. The
    8.0 s figure once cited alongside these numbers as "the true median" was
    read off audio-impact clusters; the validation task (see
    docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md) found
    those clusters track the venue's ambient noise rather than this player's
    rallies, so landing near 8.0 s is not evidence of correctness -- only a
    changed 16/7.0 s here is a signal worth investigating."""
    intervals = segment(ground_features, params_for_frames(ground_features))
    durations = sorted((iv.end_ms - iv.start_ms) / 1000 for iv in intervals)
    assert len(intervals) == 16
    assert statistics.median(durations) == pytest.approx(7.0, abs=0.5)
