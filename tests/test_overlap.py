import json
from pathlib import Path

import pytest

from splitstep.db.rallies import STAR_OVERLAP_MIN, overlap_fraction

CASES = json.loads((Path(__file__).parent / "fixtures" / "overlap_cases.json").read_text())


def test_the_gate_in_the_fixture_is_the_gate_in_the_code():
    # The fixture's `atLeastHalf` column is only meaningful against the real
    # threshold. If STAR_OVERLAP_MIN ever moves, this fails here rather than
    # letting both suites keep asserting a boolean about a gate that is gone.
    assert CASES["gate"] == STAR_OVERLAP_MIN


@pytest.mark.parametrize("case", CASES["cases"], ids=[c["name"] for c in CASES["cases"]])
def test_overlap_matches_the_shared_cases(case):
    # One case file, two implementations (see web/tests/labels.test.ts).
    # overlapFraction in web/src/lib/labels.ts is a hand-port of this
    # function, and a divergence is invisible in both directions: a client
    # drawing the line elsewhere shows a verdict `labels score` does not
    # count, or hides one it does, with no error anywhere. Exact equality,
    # not approx -- every fraction in the file round-trips through its
    # decimal literal, so both languages parse the identical double and an
    # honest port produces it bit for bit.
    (a_start, a_end), (b_start, b_end) = case["a"], case["b"]
    assert overlap_fraction(a_start, a_end, b_start, b_end) == case["fraction"]
    assert (overlap_fraction(a_start, a_end, b_start, b_end) >= STAR_OVERLAP_MIN) is case[
        "atLeastHalf"
    ]


@pytest.mark.parametrize("case", CASES["cases"], ids=[c["name"] for c in CASES["cases"]])
def test_overlap_does_not_care_which_span_came_first(case):
    # Both callers pass the pair in whichever order they hold it --
    # replace_rallies has the new interval first, the label scorer has the
    # candidate first -- so the symmetry is relied on, not incidental.
    (a_start, a_end), (b_start, b_end) = case["a"], case["b"]
    assert overlap_fraction(b_start, b_end, a_start, a_end) == case["fraction"]
