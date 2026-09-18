"""Blind, seeded sampling of windows for a labelling pass.

The corpus can only measure recall if some of the windows a reviewer judges
are windows the detector never proposed -- see
`docs/superpowers/plans/2026-09-18-unflagged-window-sampling.md`. This is the
pure half of that: which windows, chosen how, with nothing in the result
telling the reviewer which ones the detector flagged.
"""

import pytest

from splitstep.label_sample import Window, sample_windows

HOUR_MS = 3_600_000


def spans(windows):
    return [(w.start_ms, w.end_ms) for w in windows]


def overlaps(w, intervals):
    return any(w.start_ms < e and s < w.end_ms for s, e in intervals)


def test_the_same_seed_produces_the_same_sample():
    # A sitting survives a reload without a table to persist it in: the
    # sample is recomputed, not stored.
    a = sample_windows(duration_ms=HOUR_MS, intervals=[(10_000, 20_000)], n=8, seed=7)
    b = sample_windows(duration_ms=HOUR_MS, intervals=[(10_000, 20_000)], n=8, seed=7)
    assert spans(a) == spans(b)


def test_a_different_seed_produces_a_different_sample():
    a = sample_windows(duration_ms=HOUR_MS, intervals=[(10_000, 20_000)], n=8, seed=7)
    b = sample_windows(duration_ms=HOUR_MS, intervals=[(10_000, 20_000)], n=8, seed=8)
    assert spans(a) != spans(b)


def test_every_window_is_the_requested_length():
    got = sample_windows(duration_ms=HOUR_MS, intervals=[], n=10, window_ms=8000, seed=1)
    assert all(w.end_ms - w.start_ms == 8000 for w in got)


def test_every_window_lies_inside_the_source():
    got = sample_windows(duration_ms=60_000, intervals=[(0, 5000)], n=6, window_ms=8000, seed=3)
    assert all(0 <= w.start_ms and w.end_ms <= 60_000 for w in got)


def test_windows_never_overlap_each_other():
    # Two labels on overlapping footage could disagree about the same play,
    # and `latest_labels` resolves per exact span, so neither would supersede
    # the other -- the corpus would hold both.
    got = sample_windows(duration_ms=600_000, intervals=[(30_000, 40_000)], n=20, seed=5)
    ordered = sorted(spans(got))
    assert all(ordered[i][1] <= ordered[i + 1][0] for i in range(len(ordered) - 1))


def test_it_draws_from_both_the_flagged_and_the_ignored_halves():
    # The whole point: a sample of only detector intervals measures precision
    # and nothing else, which is what the corpus already could not see past.
    intervals = [(i * 60_000, i * 60_000 + 10_000) for i in range(10)]
    got = sample_windows(duration_ms=HOUR_MS, intervals=intervals, n=10, seed=2)
    flagged = [w for w in got if overlaps(w, intervals)]
    ignored = [w for w in got if not overlaps(w, intervals)]
    assert len(flagged) >= 4
    assert len(ignored) >= 4


def test_a_flagged_window_actually_overlaps_the_interval_it_came_from():
    got = sample_windows(duration_ms=HOUR_MS, intervals=[(100_000, 130_000)], n=2, seed=11)
    assert any(overlaps(w, [(100_000, 130_000)]) for w in got)


def test_an_ignored_window_overlaps_no_interval_at_all():
    intervals = [(i * 60_000, i * 60_000 + 10_000) for i in range(10)]
    got = sample_windows(duration_ms=HOUR_MS, intervals=intervals, n=10, seed=4)
    ignored = [w for w in got if not overlaps(w, intervals)]
    assert ignored
    for w in ignored:
        assert not overlaps(w, intervals)


def test_the_sample_is_shuffled_rather_than_flagged_ones_first():
    # Order is the last channel through which the sample could tell the
    # reviewer what the detector thought. The 2026-08-20 pass hid the
    # detector's guesses deliberately, and its own hand label was wrong in a
    # way only that blindness exposed.
    intervals = [(i * 60_000, i * 60_000 + 10_000) for i in range(10)]
    got = sample_windows(duration_ms=HOUR_MS, intervals=intervals, n=10, seed=6)
    kinds = [overlaps(w, intervals) for w in got]
    assert kinds != sorted(kinds, reverse=True)
    assert kinds != sorted(kinds)


def test_a_window_carries_nothing_but_its_span():
    # Not decoration: any field naming the half a window came from would
    # reach the reviewer through the API and undo the blindness above.
    assert [f for f in Window.__dataclass_fields__] == ["start_ms", "end_ms"]


def test_a_source_with_no_intervals_yields_only_ignored_windows():
    # Nothing detected at all is a legitimate state -- it is the extreme of
    # the case this exists for.
    got = sample_windows(duration_ms=600_000, intervals=[], n=6, seed=9)
    assert len(got) == 6


def test_it_returns_fewer_windows_rather_than_inventing_room():
    # A 30 s source has room for three 8 s windows, not twenty. Padding the
    # list with overlapping or out-of-range windows would put fabricated
    # spans into a corpus whose whole value is that it is hand-made.
    got = sample_windows(duration_ms=30_000, intervals=[], n=20, window_ms=8000, seed=1)
    assert 0 < len(got) <= 3


def test_a_source_shorter_than_one_window_yields_nothing():
    assert sample_windows(duration_ms=5000, intervals=[], n=5, window_ms=8000, seed=1) == []


def test_it_refuses_a_non_positive_window():
    with pytest.raises(ValueError):
        sample_windows(duration_ms=HOUR_MS, intervals=[], n=5, window_ms=0, seed=1)
