import json
from pathlib import Path

import pytest

from splitstep.score import (
    DEFAULT_RULES,
    ScoreRules,
    rules_from_dict,
    rules_to_dict,
    score,
    score_before,
    scoreboard_rows,
)

CASES = json.loads((Path(__file__).parent / "fixtures" / "score_cases.json").read_text())


def _rules(case: dict) -> ScoreRules:
    return rules_from_dict({**CASES["defaultRules"], **case.get("rules", {})})


@pytest.mark.parametrize("case", CASES["cases"], ids=[c["name"] for c in CASES["cases"]])
def test_engine_matches_the_shared_cases(case):
    # One case file, two engines (see web/tests/score.test.ts). A case that
    # passes here and fails there is the drift the shared file exists to catch.
    state = score(list(case["winners"]), _rules(case))
    e = case["expect"]
    assert [list(s) for s in state.sets] == e["sets"]
    assert list(state.games) == e["games"]
    assert list(state.points) == e["points"]
    assert state.in_tiebreak == e["inTiebreak"]
    assert state.finished == e["finished"]


def test_rules_round_trip_through_the_json_shape():
    d = {"players": ["Ann", "Bob"], "sets": 5, "ad": False, "tiebreak": "only", "tiebreakTo": 10}
    assert rules_to_dict(rules_from_dict(d)) == d


def test_rules_from_dict_rejects_bad_values():
    with pytest.raises(ValueError):
        rules_from_dict({**rules_to_dict(DEFAULT_RULES), "sets": 4})
    with pytest.raises(ValueError):
        rules_from_dict({**rules_to_dict(DEFAULT_RULES), "tiebreak": "sometimes"})
    with pytest.raises(ValueError):
        rules_from_dict({**rules_to_dict(DEFAULT_RULES), "tiebreakTo": 5})
    with pytest.raises(ValueError):
        rules_from_dict({**rules_to_dict(DEFAULT_RULES), "players": ["", "Bob"]})


def _rally(idx, *, point=1, winner="", rejected=0):
    return {"id": f"r{idx}", "idx": idx, "point": point, "winner": winner, "rejected": rejected}


def test_score_before_replays_only_earlier_scored_points_in_idx_order():
    rallies = [
        _rally(3, winner="b"),          # out of order on purpose
        _rally(1, winner="a"),
        _rally(2, winner="a"),
        _rally(4, winner="a"),          # the rally asked about: excluded
        _rally(5, winner="b"),          # after it: excluded
    ]
    state, unscored = score_before(rallies, "r4", DEFAULT_RULES)
    # Earlier, in idx order: a (r1), a (r2), b (r3) -> 15-0, 30-0, 30-15.
    assert state.points == ("30", "15")
    assert unscored == 0


def test_score_before_skips_unscored_and_rejected_points_but_counts_the_unscored():
    rallies = [
        _rally(1, winner="a"),
        _rally(2, winner=""),                    # a point nobody scored
        _rally(3, winner="b", rejected=1),       # rejected: not a rally at all
        _rally(4, point=0, winner=""),           # not a point
        _rally(5),
    ]
    state, unscored = score_before(rallies, "r5", DEFAULT_RULES)
    assert state.points == ("15", "0")
    assert unscored == 1


def test_score_before_unknown_rally_is_the_full_replay():
    # An orphaned reel item has no rally; the caller passes an id no row
    # holds and gets the state after every scored point -- never a crash.
    rallies = [_rally(1, winner="a"), _rally(2, winner="a")]
    state, _ = score_before(rallies, "nope", DEFAULT_RULES)
    assert state.points == ("30", "0")


def test_scoreboard_rows_name_sets_games_points():
    # The 40-char prefix closes the first set 6-4 (see the "set 6-4" fixture
    # case). "aaaab" then plays a love game into the second set -- 4-0 with
    # ad scoring closes a game outright, so it's games (1, 0), not a game
    # still in progress -- and the trailing "b" opens the next game at 0-15.
    state = score(list("aaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaaaaaa" + "aaaab"), DEFAULT_RULES)
    rows = scoreboard_rows(state, DEFAULT_RULES)
    assert rows == [["Me", "6", "1", "0"], ["Opp", "4", "0", "15"]]


def test_scoreboard_rows_tiebreak_only_has_no_games_column():
    rules = rules_from_dict({**rules_to_dict(DEFAULT_RULES), "tiebreak": "only"})
    rows = scoreboard_rows(score(list("aab"), rules), rules)
    assert rows == [["Me", "2"], ["Opp", "1"]]


def test_scoreboard_rows_marks_the_winner_when_finished():
    state = score(list("a" * 48), DEFAULT_RULES)
    rows = scoreboard_rows(state, DEFAULT_RULES)
    assert rows == [["Me", "6", "6", "W"], ["Opp", "0", "0", ""]]
