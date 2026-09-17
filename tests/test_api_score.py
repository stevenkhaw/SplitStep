import pytest
from fastapi.testclient import TestClient

from splitstep.api.app import create_app
from splitstep.db.rallies import list_rallies, replace_rallies
from splitstep.db.schema import connect, migrate
from splitstep.db.sessions import add_source, find_or_create_session_for_date, set_session_status
from splitstep.detect.segment import Interval

RULES = {"players": ["Me", "Opp"], "sets": 3, "ad": True, "tiebreak": "at6", "tiebreakTo": 7}


@pytest.fixture
def conn(library):
    c = connect(library.db_path)
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def client(library):
    with TestClient(create_app(library)) as c:
        yield c


@pytest.fixture
def seeded(conn):
    session_id = find_or_create_session_for_date(conn, "2026-09-17")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-09-17T10:00:00Z", duration_ms=60_000,
        width=1920, height=1080, fps=30.0, original_name="A.MOV",
    )
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7)])
    set_session_status(conn, session_id, "ready")
    return {"session_id": session_id, "source_id": source_id}


def test_session_detail_reports_null_scoring_and_empty_winners(client, seeded):
    body = client.get(f"/api/sessions/{seeded['session_id']}").json()
    assert body["session"]["scoring"] is None
    assert [r["winner"] for r in body["rallies"]] == ["", ""]


def test_scoring_round_trips_and_shows_in_both_session_routes(client, seeded):
    sid = seeded["session_id"]
    r = client.post(f"/api/sessions/{sid}/scoring", json={"rules": RULES})
    assert r.status_code == 200
    assert r.json()["scoring"] == RULES
    assert client.get(f"/api/sessions/{sid}").json()["session"]["scoring"] == RULES
    listed = [s for s in client.get("/api/sessions").json() if s["id"] == sid]
    assert listed[0]["scoring"] == RULES

    r = client.post(f"/api/sessions/{sid}/scoring", json={"rules": None})
    assert r.json()["scoring"] is None


def test_bad_rules_are_a_422(client, seeded):
    r = client.post(f"/api/sessions/{seeded['session_id']}/scoring",
                    json={"rules": {**RULES, "tiebreak": "maybe"}})
    assert r.status_code == 422


def test_scoring_404s_on_an_unknown_session(client, seeded):
    assert client.post("/api/sessions/nope/scoring", json={"rules": RULES}).status_code == 404


def test_winner_refuses_while_the_session_does_not_track(client, conn, seeded):
    rid = list_rallies(conn, seeded["session_id"])[0]["id"]
    r = client.post(f"/api/rallies/{rid}/winner", json={"winner": "a"})
    assert r.status_code == 409


def test_winner_sets_the_point_and_refreshes_status(client, conn, seeded):
    sid = seeded["session_id"]
    client.post(f"/api/sessions/{sid}/scoring", json={"rules": RULES})
    rows = list_rallies(conn, sid)
    r = client.post(f"/api/rallies/{rows[0]['id']}/winner", json={"winner": "a"})
    assert r.status_code == 200
    assert "session_status" in r.json()
    row = list_rallies(conn, sid)[0]
    assert row["winner"] == "a"
    assert row["point"] == 1


def test_winner_rejects_a_bad_value(client, conn, seeded):
    sid = seeded["session_id"]
    client.post(f"/api/sessions/{sid}/scoring", json={"rules": RULES})
    rid = list_rallies(conn, sid)[0]["id"]
    assert client.post(f"/api/rallies/{rid}/winner", json={"winner": "me"}).status_code == 422


def test_unmarking_the_point_over_the_api_clears_the_winner(client, conn, seeded):
    sid = seeded["session_id"]
    client.post(f"/api/sessions/{sid}/scoring", json={"rules": RULES})
    rid = list_rallies(conn, sid)[0]["id"]
    client.post(f"/api/rallies/{rid}/winner", json={"winner": "b"})
    client.post(f"/api/rallies/{rid}/point", json={"point": False})
    assert list_rallies(conn, sid)[0]["winner"] == ""
