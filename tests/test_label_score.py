"""Scorer arithmetic.

Synthetic inputs are correct here and do not violate the repo's rule against
synthetic fixtures: that rule bans *calibrating tuning constants* against
hand-written data, and this file fits no constant. It checks that overlap
matching, medians and the unknown count are computed the way the spec says.
"""

from splitstep.detect.segment import Interval
from splitstep.label_score import LabelRow, score_against_labels


def label(start, end, verdict="clean", true_start=None, true_end=None):
    return LabelRow(span_start_ms=start, span_end_ms=end, verdict=verdict,
                    true_start_ms=true_start, true_end_ms=true_end)


def test_a_candidate_over_a_clean_label_counts_as_precision_hit():
    s = score_against_labels([Interval(1000, 5000, 0.8)], [label(1000, 5000)])
    assert (s.matched_play, s.matched_not_play, s.unknown) == (1, 0, 0)
    assert s.precision == 1.0


def test_a_candidate_over_a_not_play_label_is_a_false_positive():
    s = score_against_labels([Interval(1000, 5000, 0.8)],
                             [label(1000, 5000, verdict="not_play")])
    assert (s.matched_play, s.matched_not_play) == (0, 1)
    assert s.precision == 0.0


def test_partly_counts_as_play():
    s = score_against_labels([Interval(1000, 5000, 0.8)],
                             [label(1000, 5000, verdict="partly")])
    assert s.matched_play == 1


def test_unsure_is_excluded_from_precision_entirely():
    # Not a hit and not a miss. Forcing an undecidable clip into either
    # column would inject noise while looking like data.
    s = score_against_labels([Interval(1000, 5000, 0.8)],
                             [label(1000, 5000, verdict="unsure")])
    assert (s.matched_play, s.matched_not_play, s.unknown) == (0, 0, 0)
    assert s.precision is None


def test_a_candidate_matching_no_label_is_unknown_not_a_false_positive():
    s = score_against_labels([Interval(60000, 65000, 0.8)], [label(1000, 5000)])
    assert s.unknown == 1
    assert (s.matched_play, s.matched_not_play) == (0, 0)
    assert s.precision is None


def test_overlap_below_the_floor_does_not_match():
    # 500 ms of overlap against a 4000 ms candidate is 12.5%.
    s = score_against_labels([Interval(4500, 8500, 0.8)], [label(1000, 5000)])
    assert s.unknown == 1


def test_a_candidate_takes_its_best_overlapping_label_when_several_qualify():
    # 0.80 overlap against 1.00 -- both clear the floor, and the closer one wins.
    s = score_against_labels(
        [Interval(1000, 5000, 0.8)],
        [label(500, 3000, verdict="not_play"), label(1000, 5000, verdict="clean")],
    )
    assert s.matched_play == 1
    assert s.matched_not_play == 0


def test_a_clean_label_with_no_candidate_is_a_miss():
    s = score_against_labels([], [label(1000, 5000), label(9000, 14000)])
    assert (s.labelled_clean, s.missed_clean) == (2, 2)
    assert s.span_recall == 0.0


def test_span_recall_is_none_when_nothing_clean_is_labelled():
    s = score_against_labels([Interval(1000, 5000, 0.8)],
                             [label(1000, 5000, verdict="not_play")])
    assert s.span_recall is None


def test_boundary_bias_is_signed_and_positive_means_the_candidate_opens_late():
    # Truth starts at 1400, candidate at 1000 -> the candidate opens 400 ms
    # early, i.e. bias -400.
    s = score_against_labels(
        [Interval(1000, 5000, 0.8)],
        [label(1000, 5000, verdict=None, true_start=1400, true_end=4600)],
    )
    assert s.start_bias_ms == -400
    assert s.end_bias_ms == 400
    assert s.boundary_n == 1


def test_boundary_mae_is_absolute_and_survives_cancelling_signs():
    s = score_against_labels(
        [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7)],
        [
            label(1000, 5000, verdict=None, true_start=1400, true_end=5000),
            label(9000, 14000, verdict=None, true_start=8600, true_end=14000),
        ],
    )
    # -400 and +400 cancel in the bias, but not in the MAE. Reporting only
    # bias would show a detector with 400 ms of scatter as perfect.
    assert s.start_bias_ms == 0
    assert s.start_mae_ms == 400


def test_boundary_stats_are_none_when_no_label_carries_a_corrected_span():
    s = score_against_labels([Interval(1000, 5000, 0.8)], [label(1000, 5000)])
    assert s.start_bias_ms is None
    assert s.start_mae_ms is None
    assert s.boundary_n == 0


def test_a_verdict_less_boundary_row_still_contributes_boundary_stats_only():
    s = score_against_labels(
        [Interval(1000, 5000, 0.8)],
        [label(1000, 5000, verdict=None, true_start=1400, true_end=4600)],
    )
    assert (s.matched_play, s.matched_not_play, s.unknown) == (0, 0, 0)
    assert s.boundary_n == 1


def test_a_not_play_label_does_not_contribute_boundary_stats():
    # A span the human said contains no play has no correct boundary to be
    # wrong about -- the human dragged the handles, but there is no "right"
    # start/end for a rally that was never there. Letting this row into
    # start_errs/end_errs would make start_bias_ms/end_mae_ms describe
    # something other than boundary accuracy, exactly the kind of metric
    # dishonesty this branch exists to eliminate elsewhere in the corpus.
    s = score_against_labels(
        [Interval(1000, 5000, 0.8)],
        [label(1000, 5000, verdict="not_play", true_start=1400, true_end=4600)],
    )
    assert s.matched_not_play == 1
    assert s.boundary_n == 0
    assert s.start_bias_ms is None
    assert s.end_bias_ms is None
    assert s.start_mae_ms is None
    assert s.end_mae_ms is None


def test_neighbouring_verdicts_still_contribute_boundary_stats():
    # Pins the boundary of the not_play exclusion above: clean/partly
    # corrections and an unsure correction must all keep counting, so a
    # future change cannot silently widen the exclusion to cover them too.
    # unsure differs from not_play in kind, not just degree -- an undecidable
    # clip may still have a real, correctly-measured edge, it is only the
    # play/no-play judgement that could not be made.
    s = score_against_labels(
        [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7), Interval(20000, 24000, 0.6)],
        [
            label(1000, 5000, verdict="clean", true_start=1400, true_end=4600),
            label(9000, 14000, verdict="partly", true_start=9400, true_end=13600),
            label(20000, 24000, verdict="unsure", true_start=20400, true_end=23600),
        ],
    )
    assert s.boundary_n == 3
