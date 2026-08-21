import pytest
from fastapi.testclient import TestClient

from bootleg.api.app import create_app
from bootleg.db.jobs import enqueue
from bootleg.db.rallies import replace_rallies, set_point, set_rejected, set_star
from bootleg.db.sessions import add_source, find_or_create_session_for_date
from bootleg.detect.segment import Interval
from bootleg.export import spans_to_cut
from bootleg.media.clips import clip_relpath


@pytest.fixture
def client(library, conn):
    with TestClient(create_app(library)) as c:
        yield c


@pytest.fixture
def seeded(library, conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id, [
        Interval(1000, 5000, 0.8),
        Interval(9000, 14000, 0.7),
        Interval(20000, 26000, 0.6),
    ])
    rows = conn.execute("SELECT * FROM rallies ORDER BY idx").fetchall()
    return {"session_id": session_id, "source_id": source_id, "idx": idx, "rallies": rows}


def _touch_clip(library, session_id, idx, start_ms, end_ms):
    path = library.clips_dir(session_id) / clip_relpath(idx, start_ms, end_ms)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not really an mp4")
    return path


def test_points_set_returns_only_points(library, conn, seeded):
    set_point(conn, seeded["rallies"][0]["id"], True)
    set_star(conn, seeded["rallies"][1]["id"], True)
    spans = spans_to_cut(library, conn, seeded["session_id"], "points")
    assert [s["start_ms"] for s in spans] == [1000]


def test_starred_set_returns_only_starred(library, conn, seeded):
    set_point(conn, seeded["rallies"][0]["id"], True)
    set_star(conn, seeded["rallies"][1]["id"], True)
    spans = spans_to_cut(library, conn, seeded["session_id"], "starred")
    assert [s["start_ms"] for s in spans] == [9000]


def test_rejected_rallies_are_never_cut(library, conn, seeded):
    # A rejection says there is no rally here, so there is nothing to cut --
    # even if the flag survived from before the rejection.
    rally = seeded["rallies"][0]
    set_point(conn, rally["id"], True)
    set_rejected(conn, rally["id"], True)
    assert spans_to_cut(library, conn, seeded["session_id"], "points") == []


def test_a_span_whose_clip_exists_is_skipped(library, conn, seeded):
    """The incremental property: this is what stops a second export
    re-encoding twenty-four clips."""
    rally = seeded["rallies"][0]
    set_point(conn, rally["id"], True)
    _touch_clip(library, seeded["session_id"], seeded["idx"], 1000, 5000)
    assert spans_to_cut(library, conn, seeded["session_id"], "points") == []


def test_a_span_whose_bounds_moved_is_cut_again(library, conn, seeded):
    # No staleness record to keep in sync: the rally now resolves to a
    # different path, and that path does not exist.
    rally = seeded["rallies"][0]
    set_point(conn, rally["id"], True)
    _touch_clip(library, seeded["session_id"], seeded["idx"], 1000, 5000)
    conn.execute("UPDATE rallies SET start_ms = 1400 WHERE id = ?", (rally["id"],))
    conn.commit()
    spans = spans_to_cut(library, conn, seeded["session_id"], "points")
    assert [s["start_ms"] for s in spans] == [1400]


def test_a_span_with_a_clip_job_in_flight_is_not_queued_twice(library, conn, seeded):
    rally = seeded["rallies"][0]
    set_point(conn, rally["id"], True)
    enqueue(conn, "clip", {"source_id": seeded["source_id"], "rally_id": rally["id"],
                           "start_ms": 1000, "end_ms": 5000})
    assert spans_to_cut(library, conn, seeded["session_id"], "points") == []


def test_spans_to_cut_rejects_an_unknown_set(library, conn, seeded):
    with pytest.raises(ValueError, match="which must be one of"):
        spans_to_cut(library, conn, seeded["session_id"], "everything")


def test_route_reports_queued_against_already_cut(client, library, conn, seeded):
    for rally in seeded["rallies"][:2]:
        set_point(conn, rally["id"], True)
    _touch_clip(library, seeded["session_id"], seeded["idx"], 1000, 5000)

    r = client.post(f"/api/sessions/{seeded['session_id']}/export",
                    json={"which": "points"})
    assert r.status_code == 200
    assert r.json() == {"queued": 1, "already_cut": 1, "total": 2}
    queued = conn.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE type = 'clip'"
    ).fetchone()["n"]
    assert queued == 1


def test_route_404s_on_an_unknown_session(client, seeded):
    r = client.post("/api/sessions/nope/export", json={"which": "points"})
    assert r.status_code == 404


def test_route_422s_on_an_unknown_set(client, seeded):
    r = client.post(f"/api/sessions/{seeded['session_id']}/export",
                    json={"which": "everything"})
    assert r.status_code == 422
