import pytest
from fastapi.testclient import TestClient

from bootleg.api.app import create_app
from bootleg.db.rallies import list_rallies, replace_rallies
from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import add_source, find_or_create_session_for_date, get_session
from bootleg.detect.segment import Interval


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
    session_id = find_or_create_session_for_date(conn, "2026-08-19")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-19T10:00:00Z", duration_ms=60_000,
        width=1920, height=1080, fps=30.0, original_name="A.MOV",
    )
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7)])
    return {"session_id": session_id, "source_id": source_id, "idx": idx}


def test_reviewed_endpoint_sets_reviewed_at(client, conn, seeded):
    rally_id = list_rallies(conn, seeded["session_id"])[0]["id"]
    r = client.post(f"/api/rallies/{rally_id}/reviewed")
    assert r.status_code == 200
    assert list_rallies(conn, seeded["session_id"])[0]["reviewed_at"] is not None


def test_session_stays_unreviewed_until_every_rally_is_seen(client, conn, seeded):
    rallies = list_rallies(conn, seeded["session_id"])
    r = client.post(f"/api/rallies/{rallies[0]['id']}/reviewed")
    assert r.json()["session_status"] != "reviewed"
    assert get_session(conn, seeded["session_id"])["status"] != "reviewed"


def test_session_becomes_reviewed_when_all_rallies_seen(client, conn, seeded):
    for rally in list_rallies(conn, seeded["session_id"]):
        r = client.post(f"/api/rallies/{rally['id']}/reviewed")
    assert r.json()["session_status"] == "reviewed"
    assert get_session(conn, seeded["session_id"])["status"] == "reviewed"


def test_starring_also_counts_as_reviewed(client, conn, seeded):
    rallies = list_rallies(conn, seeded["session_id"])
    client.post(f"/api/rallies/{rallies[0]['id']}/star", json={"starred": True})
    client.post(f"/api/rallies/{rallies[1]['id']}/reject", json={"rejected": True})
    assert get_session(conn, seeded["session_id"])["status"] == "reviewed"


def test_rejected_rallies_do_not_block_reviewed_status(client, conn, seeded):
    """A rejected rally is seen. It must not hold the session open forever."""
    for rally in list_rallies(conn, seeded["session_id"]):
        client.post(f"/api/rallies/{rally['id']}/reject", json={"rejected": True})
    assert get_session(conn, seeded["session_id"])["status"] == "reviewed"


def test_reviewed_on_unknown_rally_is_404(client):
    assert client.post("/api/rallies/nope/reviewed").status_code == 404
