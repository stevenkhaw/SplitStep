from pathlib import Path

import pytest

from splitstep.detect.segment import Interval
from splitstep.detect.truth import evaluate, fold_faults, format_report, load_truth

FIXTURE = Path(__file__).parent / "fixtures" / "truth" / "2026-10-12_source01.tsv"


def _iv(a: int, b: int) -> Interval:
    return Interval(start_ms=a, end_ms=b, confidence=1.0)


def test_load_truth_reads_covers_and_38_points():
    t = load_truth(FIXTURE)
    assert t.covers == (0, 960000)
    assert len(t.points) == 38
    assert (t.points[0].start_ms, t.points[0].end_ms) == (33000, 37000)


def test_load_truth_refuses_file_without_covers(tmp_path):
    p = tmp_path / "t.tsv"
    p.write_text("in\tout\tnote\tserver\tscore_after\n0:33\t0:37\tx\ta\t\n")
    with pytest.raises(ValueError, match="t.tsv"):
        load_truth(p)


def test_load_truth_reads_hour_times(tmp_path):
    p = tmp_path / "t.tsv"
    p.write_text(
        "# covers 0:00-1:10:00\nin\tout\tnote\tserver\tscore_after\n1:00:05\t1:00:09\tx\ta\t\n"
    )
    t = load_truth(p)
    assert t.covers == (0, 4_200_000)
    assert (t.points[0].start_ms, t.points[0].end_ms) == (3_605_000, 3_609_000)


def test_fold_faults_gives_23_points():
    t = fold_faults(load_truth(FIXTURE))
    assert len(t.points) == 23
    assert (t.points[0].start_ms, t.points[0].end_ms) == (33000, 46000)
    assert t.covers == (0, 960000)


def test_fold_faults_leaves_a_trailing_fault_alone():
    t = load_truth(FIXTURE)
    last = t.points[-1]
    tail = type(t)(t.covers, (type(last)(1000, 2000, "missed first serve", "a"),))
    assert fold_faults(tail).points == tail.points


def test_evaluate_perfect_intervals():
    t = load_truth(FIXTURE)
    r = evaluate([_iv(p.start_ms, p.end_ms) for p in t.points], t)
    assert (r.caught, r.total) == (38, 38)
    assert r.play_share == 1.0
    assert r.median_start_err_s == 0.0
    assert r.median_end_err_s == 0.0
    assert r.missed == ()


def test_evaluate_counts_half_coverage_as_caught():
    t = load_truth(FIXTURE)
    r = evaluate([_iv(33000, 35000)], t)
    assert r.caught == 1
    r = evaluate([_iv(33000, 34999)], t)
    assert r.caught == 0
    assert r.missed[0] == 33000


def test_evaluate_ignores_time_outside_covers():
    t = load_truth(FIXTURE)
    base = [_iv(p.start_ms, p.end_ms) for p in t.points]
    r = evaluate(base + [_iv(1_200_000, 1_300_000)], t)
    assert r.caught == 38
    assert r.play_share == 1.0
    # An interval straddling the edge counts only for the part inside.
    r = evaluate(base + [_iv(950_000, 1_000_000)], t)
    assert r.play_share == pytest.approx(
        sum(p.end_ms - p.start_ms for p in t.points)
        / (sum(p.end_ms - p.start_ms for p in t.points) + 10_000)
    )


def test_format_report_uses_spec_metric_names():
    t = load_truth(FIXTURE)
    line = format_report("src", "label", evaluate([_iv(33000, 37000)], t))
    assert "points caught" in line and "1/38" in line
    assert "clip time that is play" in line
    assert "median start error" in line and "median end error" in line
    assert "\n" not in line
