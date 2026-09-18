import pytest

from splitstep.db.rallies import (
    list_rallies,
    replace_rallies,
    set_point,
    set_winner,
    split_rally,
)
from splitstep.db.sessions import (
    add_source,
    find_or_create_session_for_date,
    get_session,
    scoring_rules,
    set_scoring,
)
from splitstep.detect.segment import Interval

RULES = {"players": ["Me", "Opp"], "sets": 3, "ad": True, "tiebreak": "at6", "tiebreakTo": 7,
         "firstServer": None}


@pytest.fixture
def seeded(conn):
    session_id = find_or_create_session_for_date(conn, "2026-09-17")
    source_id, _idx = add_source(
        conn, session_id, recorded_at="2026-09-17T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_1.MOV",
    )
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7)])
    return {"session_id": session_id, "source_id": source_id}


def _rallies(conn, session_id):
    return list_rallies(conn, session_id)


def test_winner_defaults_to_empty(conn, seeded):
    assert _rallies(conn, seeded["session_id"])[0]["winner"] == ""


def test_set_winner_also_marks_the_point_and_stamps_review(conn, seeded):
    rid = _rallies(conn, seeded["session_id"])[0]["id"]
    set_winner(conn, rid, "a")
    row = _rallies(conn, seeded["session_id"])[0]
    assert row["winner"] == "a"
    assert row["point"] == 1
    assert row["reviewed_at"] is not None
    assert row["seen_at"] is not None


def test_clearing_the_winner_leaves_the_point(conn, seeded):
    rid = _rallies(conn, seeded["session_id"])[0]["id"]
    set_winner(conn, rid, "a")
    set_winner(conn, rid, "")
    row = _rallies(conn, seeded["session_id"])[0]
    assert row["winner"] == ""
    assert row["point"] == 1


def test_unmarking_the_point_clears_the_winner(conn, seeded):
    # The two columns must not disagree about whether a point was scored.
    rid = _rallies(conn, seeded["session_id"])[0]["id"]
    set_winner(conn, rid, "b")
    set_point(conn, rid, False)
    row = _rallies(conn, seeded["session_id"])[0]
    assert row["point"] == 0
    assert row["winner"] == ""


def test_set_winner_rejects_anything_but_a_b_or_empty(conn, seeded):
    rid = _rallies(conn, seeded["session_id"])[0]["id"]
    with pytest.raises(ValueError):
        set_winner(conn, rid, "me")


def test_replace_rallies_carries_the_winner_with_the_point(conn, seeded):
    rid = _rallies(conn, seeded["session_id"])[0]["id"]
    set_winner(conn, rid, "a")
    # Shifted by 300ms: well over the 50% overlap the flags carry across on.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1300, 5300, 0.8), Interval(9000, 14000, 0.7)])
    rows = _rallies(conn, seeded["session_id"])
    assert rows[0]["point"] == 1
    assert rows[0]["winner"] == "a"
    assert rows[1]["winner"] == ""


def test_replace_rallies_drops_the_winner_when_no_new_rally_overlaps(conn, seeded):
    rid = _rallies(conn, seeded["session_id"])[0]["id"]
    set_winner(conn, rid, "a")
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(20000, 25000, 0.8)])
    rows = _rallies(conn, seeded["session_id"])
    assert rows[0]["point"] == 0
    assert rows[0]["winner"] == ""


def test_split_inherits_the_winner_on_both_halves(conn, seeded):
    rid = _rallies(conn, seeded["session_id"])[0]["id"]
    set_winner(conn, rid, "b")
    split_rally(conn, rid, 3000)
    rows = _rallies(conn, seeded["session_id"])
    assert [r["winner"] for r in rows[:2]] == ["b", "b"]


def test_session_scoring_round_trips_and_clears(conn, seeded):
    sid = seeded["session_id"]
    assert scoring_rules(get_session(conn, sid)) is None
    set_scoring(conn, sid, RULES)
    assert scoring_rules(get_session(conn, sid)) == RULES
    set_scoring(conn, sid, None)
    assert scoring_rules(get_session(conn, sid)) is None
    assert get_session(conn, sid)["scoring"] == ""


def test_set_scoring_validates(conn, seeded):
    with pytest.raises(ValueError):
        set_scoring(conn, seeded["session_id"], {**RULES, "sets": 2})
