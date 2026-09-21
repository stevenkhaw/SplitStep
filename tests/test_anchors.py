"""Score anchors (migration 017): a reviewer's "entering here, the score is
X", stored as an input to the replay and keyed on a time on a source."""

import pytest
from fastapi.testclient import TestClient

from splitstep.api.app import create_app
from splitstep.db.anchors import delete_anchor, list_anchors, set_anchor
from splitstep.db.rallies import list_rallies, replace_rallies, set_winner
from splitstep.db.sessions import add_source, find_or_create_session_for_date, set_scoring
from splitstep.detect.segment import Interval
from splitstep.score import DEFAULT_RULES, Anchor, AnchorState, score_before

RULES = {"players": ["Me", "Opp"], "sets": 3, "ad": True, "tiebreak": "at6", "tiebreakTo": 7,
         "firstServer": None}
STATE = {"sets": [[6, 4]], "games": [3, 2], "points": [2, 1]}


@pytest.fixture
def client(library):
    with TestClient(create_app(library)) as c:
        yield c


@pytest.fixture
def seeded(conn):
    session_id = find_or_create_session_for_date(conn, "2026-09-20")
    one, _ = add_source(
        conn, session_id, recorded_at="2026-09-20T10:00:00Z", duration_ms=600_000,
        width=1920, height=1080, fps=30.0, original_name="A.MOV",
    )
    two, _ = add_source(
        conn, session_id, recorded_at="2026-09-20T10:30:00Z", duration_ms=600_000,
        width=1920, height=1080, fps=30.0, original_name="B.MOV",
    )
    spans = [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7), Interval(20000, 25000, 0.7)]
    replace_rallies(conn, session_id, one, spans)
    replace_rallies(conn, session_id, two, spans)
    for r in list_rallies(conn, session_id):
        set_winner(conn, r["id"], "a")
    set_scoring(conn, session_id, RULES)
    return {"session_id": session_id, "one": one, "two": two}


def test_score_anchors_is_keyed_on_a_time_and_cascades_only_from_sources(conn):
    # The reel_items lesson (migration 007): no rally id, no rally foreign
    # key, so replace_rallies deleting every rally cannot touch a row here.
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(score_anchors)")}
    assert "rally_id" not in cols
    assert {"source_id", "at_ms", "state", "created_at"} <= cols
    fks = conn.execute("PRAGMA foreign_key_list(score_anchors)").fetchall()
    assert [(fk["table"], fk["on_delete"]) for fk in fks] == [("sources", "CASCADE")]


def test_set_anchor_round_trips_and_lists_in_source_then_time_order(conn, seeded):
    set_anchor(conn, seeded["two"], 9000, AnchorState(((6, 4),), (3, 2), (2, 1)))
    set_anchor(conn, seeded["one"], 20000, AnchorState((), (1, 0), (0, 0)))
    set_anchor(conn, seeded["one"], 1000, AnchorState((), (0, 0), (3, 3)))
    got = list_anchors(conn, seeded["session_id"])
    assert [(a.source_id, a.at_ms) for a in got] == [
        (seeded["one"], 1000), (seeded["one"], 20000), (seeded["two"], 9000),
    ]
    assert got[2].state == AnchorState(((6, 4),), (3, 2), (2, 1))
    assert all(isinstance(a, Anchor) for a in got)


def test_set_anchor_at_the_same_time_replaces_rather_than_duplicates(conn, seeded):
    set_anchor(conn, seeded["one"], 9000, AnchorState((), (1, 0), (0, 0)))
    set_anchor(conn, seeded["one"], 9000, AnchorState((), (2, 0), (0, 0)))
    got = list_anchors(conn, seeded["session_id"])
    assert len(got) == 1
    assert got[0].state.games == (2, 0)


def test_delete_anchor_removes_exactly_one(conn, seeded):
    set_anchor(conn, seeded["one"], 9000, AnchorState((), (1, 0), (0, 0)))
    set_anchor(conn, seeded["one"], 20000, AnchorState((), (2, 0), (0, 0)))
    assert delete_anchor(conn, seeded["one"], 9000) is True
    assert delete_anchor(conn, seeded["one"], 9000) is False
    assert [a.at_ms for a in list_anchors(conn, seeded["session_id"])] == [20000]


def test_list_anchors_is_scoped_to_the_session(conn, seeded):
    other = find_or_create_session_for_date(conn, "2026-09-21")
    src, _ = add_source(
        conn, other, recorded_at="2026-09-21T10:00:00Z", duration_ms=1000,
        width=1920, height=1080, fps=30.0, original_name="C.MOV",
    )
    set_anchor(conn, src, 0, AnchorState((), (5, 5), (0, 0)))
    assert list_anchors(conn, seeded["session_id"]) == []


def test_an_anchor_survives_a_resegment(conn, seeded):
    # replace_rallies deletes every rally for the source and rebuilds them
    # with new ids and moved boundaries. The anchor is a time, and the
    # score still lands on the same footage.
    sid, one = seeded["session_id"], seeded["one"]
    rows = [dict(r) for r in list_rallies(conn, sid)]
    second = rows[1]
    set_anchor(conn, one, second["start_ms"], AnchorState(((6, 4),), (3, 2), (0, 0)))
    state, _ = score_before(rows, second["id"], DEFAULT_RULES, list_anchors(conn, sid))
    assert state.games == (3, 2)

    replace_rallies(conn, sid, one, [
        Interval(800, 5200, 0.8), Interval(8700, 14300, 0.7), Interval(19900, 25100, 0.7),
    ])
    rows = [dict(r) for r in list_rallies(conn, sid)]
    assert rows[1]["id"] != second["id"]
    assert rows[1]["start_ms"] == 8700
    state, _ = score_before(rows, rows[1]["id"], DEFAULT_RULES, list_anchors(conn, sid))
    assert state.sets == ((6, 4),)
    assert state.games == (3, 2)
    assert state.points == ("0", "0")
    # And the rally after it builds on the anchor, not on love-all.
    state, _ = score_before(rows, rows[2]["id"], DEFAULT_RULES, list_anchors(conn, sid))
    assert state.points == ("15", "0")


def test_session_detail_serves_the_anchors_flattened(client, conn, seeded):
    sid = seeded["session_id"]
    assert client.get(f"/api/sessions/{sid}").json()["anchors"] == []
    set_anchor(conn, seeded["one"], 9000, AnchorState(((6, 4),), (3, 2), (2, 1)))
    assert client.get(f"/api/sessions/{sid}").json()["anchors"] == [
        {"source_id": seeded["one"], "at_ms": 9000, **STATE},
    ]


def test_post_anchor_writes_and_returns_the_sessions_anchors(client, conn, seeded):
    r = client.post(f"/api/sources/{seeded['one']}/anchors", json={"at_ms": 9000, **STATE})
    assert r.status_code == 200
    assert r.json()["anchors"] == [{"source_id": seeded["one"], "at_ms": 9000, **STATE}]
    got = list_anchors(conn, seeded["session_id"])
    assert len(got) == 1
    assert got[0].state == AnchorState(((6, 4),), (3, 2), (2, 1))


def test_post_anchor_rejects_a_malformed_state_with_a_422(client, seeded):
    bad = [
        {"at_ms": 9000, "sets": [[6]], "games": [3, 2], "points": [2, 1]},
        {"at_ms": 9000, "sets": [], "games": [-1, 2], "points": [2, 1]},
        {"at_ms": 9000, "sets": [], "games": [1, 2], "points": ["40", "15"]},
        {"at_ms": -5, **STATE},
    ]
    for body in bad:
        assert client.post(f"/api/sources/{seeded['one']}/anchors", json=body).status_code == 422


def test_anchor_routes_404_on_an_unknown_source(client, seeded):
    assert client.post("/api/sources/nope/anchors", json={"at_ms": 0, **STATE}).status_code == 404
    assert client.delete("/api/sources/nope/anchors/0").status_code == 404


def test_delete_anchor_route_removes_it_and_returns_the_rest(client, conn, seeded):
    one = seeded["one"]
    set_anchor(conn, one, 9000, AnchorState((), (1, 0), (0, 0)))
    set_anchor(conn, one, 20000, AnchorState((), (2, 0), (0, 0)))
    r = client.delete(f"/api/sources/{one}/anchors/9000")
    assert r.status_code == 200
    assert [a["at_ms"] for a in r.json()["anchors"]] == [20000]
    # Deleting what is not there is a 404, not a silent 200: the client
    # shows "removed" on success and must not claim it for a no-op.
    assert client.delete(f"/api/sources/{one}/anchors/9000").status_code == 404
