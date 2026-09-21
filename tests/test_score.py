import json
from pathlib import Path

import pytest

from splitstep.score import (
    DEFAULT_RULES,
    Anchor,
    ScoreRules,
    anchor_from_dict,
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
    # `anchor`, when a case carries one, is the state the replay starts
    # from instead of love-all -- a reviewer's "entering here the score is
    # X" (migration 017). Raw point counts, never labels.
    anchor = anchor_from_dict(case["anchor"]) if "anchor" in case else None
    state = score(list(case["winners"]), _rules(case), anchor)
    e = case["expect"]
    assert [list(s) for s in state.sets] == e["sets"]
    assert list(state.games) == e["games"]
    assert list(state.points) == e["points"]
    assert state.in_tiebreak == e["inTiebreak"]
    assert state.finished == e["finished"]
    assert state.server == e["server"]


def test_rules_round_trip_through_the_json_shape():
    d = {"players": ["Ann", "Bob"], "sets": 5, "ad": False, "tiebreak": "only", "tiebreakTo": 10,
         "firstServer": "b"}
    assert rules_to_dict(rules_from_dict(d)) == d


def test_first_server_is_optional_and_never_guessed():
    # Every session recorded before this existed has no first server, and
    # there is no honest value to backfill -- defaulting to 'a' would be
    # wrong half the time. Absent stays absent, and the key round-trips as
    # null so the column's shape is the same either way.
    d = {"players": ["Ann", "Bob"], "sets": 3, "ad": True, "tiebreak": "at6", "tiebreakTo": 7}
    assert rules_from_dict(d).first_server is None
    assert rules_to_dict(rules_from_dict(d)) == {**d, "firstServer": None}
    assert DEFAULT_RULES.first_server is None


def test_rules_from_dict_rejects_a_first_server_that_is_not_a_player():
    with pytest.raises(ValueError):
        rules_from_dict({**rules_to_dict(DEFAULT_RULES), "firstServer": "c"})
    with pytest.raises(ValueError):
        rules_from_dict({**rules_to_dict(DEFAULT_RULES), "firstServer": ""})


def test_rules_from_dict_rejects_bad_values():
    with pytest.raises(ValueError):
        rules_from_dict({**rules_to_dict(DEFAULT_RULES), "sets": 4})
    with pytest.raises(ValueError):
        rules_from_dict({**rules_to_dict(DEFAULT_RULES), "tiebreak": "sometimes"})
    with pytest.raises(ValueError):
        rules_from_dict({**rules_to_dict(DEFAULT_RULES), "tiebreakTo": 5})
    with pytest.raises(ValueError):
        rules_from_dict({**rules_to_dict(DEFAULT_RULES), "players": ["", "Bob"]})


def _rally(idx, *, point=1, winner="", rejected=0, source_id="src", start_ms=None, end_ms=None):
    # Ten seconds apart, eight long, unless a test places the rally itself.
    start = idx * 10_000 if start_ms is None else start_ms
    end = start + 8_000 if end_ms is None else end_ms
    return {"id": f"r{idx}", "idx": idx, "point": point, "winner": winner, "rejected": rejected,
            "source_id": source_id, "start_ms": start, "end_ms": end}


def _anchor(at_ms, sets=(), games=(0, 0), points=(0, 0), source_id="src"):
    return Anchor(source_id, at_ms, anchor_from_dict(
        {"sets": [list(s) for s in sets], "games": list(games), "points": list(points)}))


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


def test_an_anchor_resets_the_replay_and_later_rallies_build_on_it():
    rallies = [_rally(i, winner="a") for i in range(1, 6)]
    # Without the anchor, r4 would be entered at 40-0 (three points to a).
    anchor = _anchor(rallies[2]["start_ms"], sets=[(6, 4)], games=(2, 3), points=(0, 1))
    state, _ = score_before(rallies, "r3", DEFAULT_RULES, [anchor])
    assert state.sets == ((6, 4),)
    assert state.games == (2, 3)
    assert state.points == ("0", "15")
    # r3 and r4 both go to a on top of the anchor: 15-15, 30-15 entering r5.
    state, _ = score_before(rallies, "r5", DEFAULT_RULES, [anchor])
    assert state.games == (2, 3)
    assert state.points == ("30", "15")


def test_rallies_before_an_anchor_are_untouched_by_it():
    rallies = [_rally(i, winner="a") for i in range(1, 6)]
    anchor = _anchor(rallies[3]["start_ms"], games=(5, 5))
    state, _ = score_before(rallies, "r3", DEFAULT_RULES, [anchor])
    assert state.games == (0, 0)
    assert state.points == ("30", "0")


def test_the_newest_anchor_at_or_before_the_rally_wins():
    rallies = [_rally(i, winner="a") for i in range(1, 6)]
    early = _anchor(rallies[0]["start_ms"], games=(1, 0))
    late = _anchor(rallies[2]["start_ms"], games=(4, 4))
    state, _ = score_before(rallies, "r4", DEFAULT_RULES, [late, early])
    assert state.games == (4, 4)
    assert state.points == ("15", "0")
    # Two anchors inside one rally's span: the later time wins.
    twin = _anchor(rallies[2]["start_ms"] + 500, games=(2, 2))
    state, _ = score_before(rallies, "r4", DEFAULT_RULES, [late, twin, early])
    assert state.games == (2, 2)


def test_an_anchor_inside_a_rally_applies_to_that_rally():
    # The anchor is a time, and a rally straddling it is the rally it
    # names -- so a re-segment that nudges the boundary either side of the
    # anchor keeps it on the same footage.
    rallies = [_rally(i, winner="a") for i in range(1, 4)]
    mid = (rallies[1]["start_ms"] + rallies[1]["end_ms"]) // 2
    state, _ = score_before(rallies, "r2", DEFAULT_RULES, [_anchor(mid, games=(3, 0))])
    assert state.games == (3, 0)
    assert state.points == ("0", "0")


def test_an_anchor_in_a_gap_applies_to_the_next_rally():
    rallies = [_rally(i, winner="a") for i in range(1, 4)]
    gap = rallies[0]["end_ms"] + 1_000
    state, _ = score_before(rallies, "r2", DEFAULT_RULES, [_anchor(gap, games=(3, 0))])
    assert state.games == (3, 0)
    state, _ = score_before(rallies, "r1", DEFAULT_RULES, [_anchor(gap, games=(3, 0))])
    assert state.games == (0, 0)


def test_anchors_on_different_sources_do_not_leak():
    # Two sources, session idx running across both. An anchor on the second
    # source at a time that also exists on the first must not fire there.
    rallies = [
        _rally(1, winner="a", source_id="one", start_ms=1_000, end_ms=5_000),
        _rally(2, winner="a", source_id="one", start_ms=9_000, end_ms=14_000),
        _rally(3, winner="a", source_id="two", start_ms=1_000, end_ms=5_000),
        _rally(4, winner="a", source_id="two", start_ms=9_000, end_ms=14_000),
    ]
    anchor = _anchor(1_000, games=(5, 5), source_id="two")
    state, _ = score_before(rallies, "r2", DEFAULT_RULES, [anchor])
    assert state.games == (0, 0)
    assert state.points == ("15", "0")
    state, _ = score_before(rallies, "r4", DEFAULT_RULES, [anchor])
    assert state.games == (5, 5)
    assert state.points == ("15", "0")


def test_an_anchor_after_a_sources_last_rally_carries_into_the_next_source():
    rallies = [
        _rally(1, winner="a", source_id="one", start_ms=1_000, end_ms=5_000),
        _rally(2, winner="a", source_id="two", start_ms=1_000, end_ms=5_000),
    ]
    anchor = _anchor(50_000, games=(2, 2), source_id="one")
    state, _ = score_before(rallies, "r2", DEFAULT_RULES, [anchor])
    assert state.games == (2, 2)
    assert state.points == ("0", "0")


def test_an_anchor_on_a_source_with_no_rallies_is_ignored():
    # Nothing places it in the session's order, so it cannot honestly
    # apply anywhere.
    rallies = [_rally(1, winner="a")]
    state, _ = score_before(rallies, "", DEFAULT_RULES, [_anchor(0, games=(3, 3), source_id="x")])
    assert state.games == (0, 0)
    assert state.points == ("15", "0")


def test_the_full_replay_honours_anchors_too():
    rallies = [_rally(i, winner="a") for i in range(1, 4)]
    anchor = _anchor(rallies[1]["start_ms"], games=(1, 1))
    state, unscored = score_before(rallies, "", DEFAULT_RULES, [anchor])
    assert state.games == (1, 1)
    assert state.points == ("30", "0")
    assert unscored == 0


def test_unscored_counts_only_points_after_the_anchor():
    # Points the anchor supersedes are no longer provisional: the reviewer
    # has stated the score, so a missing winner before it is not a gap.
    rallies = [_rally(1), _rally(2, winner="a"), _rally(3)]
    anchor = _anchor(rallies[1]["start_ms"])
    _, unscored = score_before(rallies, "", DEFAULT_RULES, [anchor])
    assert unscored == 1


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
