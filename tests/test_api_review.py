import pytest
from fastapi.testclient import TestClient

from bootleg.api.app import create_app
from bootleg.db.rallies import list_rallies, mark_reviewed, replace_rallies
from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import (
    add_source,
    find_or_create_session_for_date,
    get_session,
    refresh_session_review_status,
    set_session_status,
)
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
    # Review actions only ever move a session that the job pipeline has
    # already handed off -- see refresh_session_review_status's status
    # guard. This mirrors handlers.py setting a session to 'ready' once
    # every source in it is ready, which is when review begins in
    # practice; a fresh session otherwise starts 'ingesting'.
    set_session_status(conn, session_id, "ready")
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


def test_star_on_unknown_rally_is_404(client):
    assert client.post(
        "/api/rallies/nope/star", json={"starred": True}
    ).status_code == 404


def test_reject_on_unknown_rally_is_404(client):
    assert client.post(
        "/api/rallies/nope/reject", json={"rejected": True}
    ).status_code == 404


def test_detecting_session_is_not_touched_by_review_actions(client, conn, seeded):
    """Rally rows are addressable before a source reaches 'ready' --
    handlers.py calls replace_rallies before marking a source ready -- and
    a multi-source session stays 'detecting' while a sibling source is
    still processing. Reviewing every rally on the finished source must
    not paper over that.
    """
    add_source(
        conn, seeded["session_id"], recorded_at="2026-08-19T11:00:00Z",
        duration_ms=30_000, width=1920, height=1080, fps=30.0,
        original_name="B.MOV",
    )
    set_session_status(conn, seeded["session_id"], "detecting")

    r = None
    for rally in list_rallies(conn, seeded["session_id"]):
        r = client.post(f"/api/rallies/{rally['id']}/reviewed")
    assert r.json()["session_status"] == "detecting"
    assert get_session(conn, seeded["session_id"])["status"] == "detecting"


def test_failed_session_survives_star_reject_and_reviewed(client, conn, seeded):
    """A review action must never mask a source that failed to process."""
    set_session_status(conn, seeded["session_id"], "failed")
    rallies = list_rallies(conn, seeded["session_id"])

    r1 = client.post(f"/api/rallies/{rallies[0]['id']}/star", json={"starred": True})
    assert r1.json()["session_status"] == "failed"
    assert get_session(conn, seeded["session_id"])["status"] == "failed"

    r2 = client.post(f"/api/rallies/{rallies[1]['id']}/reject", json={"rejected": True})
    assert r2.json()["session_status"] == "failed"
    assert get_session(conn, seeded["session_id"])["status"] == "failed"

    r3 = client.post(f"/api/rallies/{rallies[0]['id']}/reviewed")
    assert r3.json()["session_status"] == "failed"
    assert get_session(conn, seeded["session_id"])["status"] == "failed"


def test_unstarring_a_reviewed_session_stays_reviewed(client, conn, seeded):
    """Un-starring does not un-see a rally."""
    rallies = list_rallies(conn, seeded["session_id"])
    client.post(f"/api/rallies/{rallies[0]['id']}/star", json={"starred": True})
    client.post(f"/api/rallies/{rallies[1]['id']}/reviewed")
    assert get_session(conn, seeded["session_id"])["status"] == "reviewed"

    r = client.post(f"/api/rallies/{rallies[0]['id']}/star", json={"starred": False})
    assert r.json()["session_status"] == "reviewed"
    assert get_session(conn, seeded["session_id"])["status"] == "reviewed"


def test_zero_rally_session_never_becomes_reviewed(conn):
    """Detection finding nothing is not review being done -- it is a
    session whose threshold needs lowering. It stays 'ready' forever.
    """
    session_id = find_or_create_session_for_date(conn, "2026-08-20")
    set_session_status(conn, session_id, "ready")

    status = refresh_session_review_status(conn, session_id)

    assert status == "ready"
    assert get_session(conn, session_id)["status"] == "ready"


def test_refresh_uses_a_single_atomic_write(conn, seeded):
    """Two review actions on the last two rallies in a session -- the
    normal way every session ends -- run on separate threads with separate
    connections (ThreadLocalConnections). A read-then-write could let both
    read a stale unseen count and both write 'ready', after which nothing
    is left to click and the session would never reach 'reviewed'. Guard
    against regressing back to that by asserting the whole verdict is
    computed and applied in exactly one write statement.
    """
    for rally in list_rallies(conn, seeded["session_id"]):
        mark_reviewed(conn, rally["id"])

    statements = []
    conn.set_trace_callback(statements.append)
    try:
        refresh_session_review_status(conn, seeded["session_id"])
    finally:
        conn.set_trace_callback(None)

    writes = [
        s for s in statements
        if s.strip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
    ]
    assert len(writes) == 1
