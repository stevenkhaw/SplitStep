"""The HTTP route behind timeline's add-a-rally mode.

`create_rally` already refuses both an unknown source and an incoherent
span, and it raises one exception type for both. The route's whole job on
top of that is telling those two apart: see `api_split`'s docstring for why
they must not collapse into one status.
"""
import pytest
from fastapi.testclient import TestClient

from splitstep.api.app import create_app
from splitstep.db.rallies import MIN_RALLY_MS, replace_rallies
from splitstep.db.sessions import add_source, find_or_create_session_for_date
from splitstep.detect.segment import Interval

DURATION_MS = 600_000


@pytest.fixture
def client(library, conn):
    # Lifespan startup/shutdown only run inside the context manager, and
    # shutdown is what closes the per-thread connection pool.
    with TestClient(create_app(library)) as c:
        yield c


@pytest.fixture
def seeded(library, conn):
    session_id = find_or_create_session_for_date(conn, "2026-09-19")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-09-19T10:00:00Z", duration_ms=DURATION_MS,
        width=3840, height=2160, fps=30.0, original_name="IMG_9100.MOV",
    )
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7)])
    return {"session_id": session_id, "source_id": source_id, "idx": idx}


def test_a_good_span_returns_the_new_rally_id(client, conn, seeded):
    r = client.post(f"/api/sources/{seeded['source_id']}/rallies",
                    json={"start_ms": 20_000, "end_ms": 26_000})
    assert r.status_code == 200

    rally_id = r.json()["rally_id"]
    row = conn.execute("SELECT * FROM rallies WHERE id = ?", (rally_id,)).fetchone()
    assert row is not None
    assert (row["start_ms"], row["end_ms"]) == (20_000, 26_000)


def test_an_unknown_source_is_a_404(client, seeded):
    # A stale client: the source id it holds no longer exists. The reviewer's
    # recovery is a reload, which is a different recovery from a bad span's.
    r = client.post("/api/sources/nope/rallies", json={"start_ms": 20_000, "end_ms": 26_000})
    assert r.status_code == 404


def test_a_too_short_span_is_a_400(client, seeded):
    # A live client asking for something incoherent -- a stray double-tap of
    # `Enter` with in and out on the same frame. The reviewer moves the
    # playhead; nothing is stale.
    r = client.post(f"/api/sources/{seeded['source_id']}/rallies",
                    json={"start_ms": 20_000, "end_ms": 20_000 + MIN_RALLY_MS - 1})
    assert r.status_code == 400


def test_a_span_past_the_source_duration_is_a_400(client, seeded):
    r = client.post(f"/api/sources/{seeded['source_id']}/rallies",
                    json={"start_ms": DURATION_MS - 1000, "end_ms": DURATION_MS + 5000})
    assert r.status_code == 400


def test_the_new_rally_comes_back_in_the_session_detail_with_null_det_bounds(client, seeded):
    rally_id = client.post(f"/api/sources/{seeded['source_id']}/rallies",
                           json={"start_ms": 20_000, "end_ms": 26_000}).json()["rally_id"]

    detail = client.get(f"/api/sessions/{seeded['session_id']}").json()
    added = [r for r in detail["rallies"] if r["id"] == rally_id]
    assert len(added) == 1
    # The absence of a detector span is the entire "made by a human" marker,
    # and every client-side consumer of it reads the session detail.
    assert added[0]["det_start_ms"] is None
    assert added[0]["det_end_ms"] is None
