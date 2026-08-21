import pytest
from fastapi.testclient import TestClient

from bootleg.api.app import create_app
from bootleg.db.labels import add_label
from bootleg.db.rallies import replace_rallies
from bootleg.db.sessions import add_source, find_or_create_session_for_date
from bootleg.detect.segment import Interval


@pytest.fixture
def client(library, conn):
    # Lifespan startup/shutdown only run inside the context manager, and
    # shutdown is what closes the per-thread connection pool.
    with TestClient(create_app(library)) as c:
        yield c


@pytest.fixture
def seeded(library, conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=1920, height=1080, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7)])
    return {"session_id": session_id, "source_id": source_id, "idx": idx}


def _first_rally(conn):
    return conn.execute("SELECT * FROM rallies ORDER BY idx").fetchone()


def test_label_route_anchors_to_the_detector_span_not_the_edited_bounds(client, conn, seeded):
    rally = _first_rally(conn)
    client.post(f"/api/rallies/{rally['id']}/bounds",
                json={"start_ms": 1400, "end_ms": 4600})

    r = client.post(f"/api/rallies/{rally['id']}/label",
                    json={"verdict": "clean", "boundary_flags": []})
    assert r.status_code == 200

    rows = client.get(f"/api/sources/{seeded['source_id']}/labels").json()
    verdict_rows = [row for row in rows if row["verdict"] == "clean"]
    assert len(verdict_rows) == 1
    # det span, not the 1400/4600 the reviewer dragged to.
    assert verdict_rows[0]["span_start_ms"] == 1000
    assert verdict_rows[0]["span_end_ms"] == 5000


def test_label_route_stores_boundary_flags(client, conn, seeded):
    rally = _first_rally(conn)
    client.post(f"/api/rallies/{rally['id']}/label",
                json={"verdict": "partly", "boundary_flags": ["end_late", "start_early"]})

    rows = client.get(f"/api/sources/{seeded['source_id']}/labels").json()
    assert rows[0]["boundary_flags"] == ["start_early", "end_late"]


def test_label_route_rejects_an_unknown_verdict(client, conn, seeded):
    rally = _first_rally(conn)
    r = client.post(f"/api/rallies/{rally['id']}/label",
                    json={"verdict": "probably", "boundary_flags": []})
    assert r.status_code == 422


def test_label_route_rejects_an_unknown_boundary_flag(client, conn, seeded):
    rally = _first_rally(conn)
    r = client.post(f"/api/rallies/{rally['id']}/label",
                    json={"verdict": "clean", "boundary_flags": ["start_slightly_early"]})
    assert r.status_code == 422


def test_label_route_404s_on_a_rally_a_resegment_deleted(client, conn, seeded):
    rally = _first_rally(conn)
    replace_rallies(conn, seeded["session_id"], seeded["source_id"], [Interval(2000, 6000, 0.9)])
    r = client.post(f"/api/rallies/{rally['id']}/label",
                    json={"verdict": "clean", "boundary_flags": []})
    assert r.status_code == 404


def test_source_labels_returns_only_the_latest_row_per_span(client, conn, seeded):
    rally = _first_rally(conn)
    client.post(f"/api/rallies/{rally['id']}/label",
                json={"verdict": "not_play", "boundary_flags": []})
    client.post(f"/api/rallies/{rally['id']}/label",
                json={"verdict": "clean", "boundary_flags": []})

    rows = client.get(f"/api/sources/{seeded['source_id']}/labels").json()
    assert len(rows) == 1
    assert rows[0]["verdict"] == "clean"


def test_source_labels_reports_a_boundary_only_row_with_a_null_verdict(client, conn, seeded):
    add_label(conn, source_id=seeded["source_id"], span_start_ms=1000, span_end_ms=5000,
              verdict=None, boundary_flags=["end_late"], true_start_ms=1000,
              true_end_ms=4600)
    rows = client.get(f"/api/sources/{seeded['source_id']}/labels").json()
    assert rows[0]["verdict"] is None
    assert rows[0]["true_end_ms"] == 4600


def test_source_labels_404s_on_an_unknown_source(client, seeded):
    r = client.get("/api/sources/nope/labels")
    assert r.status_code == 404


def test_a_boundary_drag_records_a_signed_correction(client, conn, seeded):
    rally = _first_rally(conn)
    client.post(f"/api/rallies/{rally['id']}/bounds",
                json={"start_ms": 1400, "end_ms": 4600})

    rows = client.get(f"/api/sources/{seeded['source_id']}/labels").json()
    assert len(rows) == 1
    assert rows[0]["verdict"] is None
    assert rows[0]["span_start_ms"] == 1000
    assert rows[0]["span_end_ms"] == 5000
    assert rows[0]["true_start_ms"] == 1400
    assert rows[0]["true_end_ms"] == 4600
    assert rows[0]["boundary_flags"] == ["start_early", "end_late"]


def test_a_drag_back_to_the_detector_span_records_nothing(client, conn, seeded):
    rally = _first_rally(conn)
    client.post(f"/api/rallies/{rally['id']}/bounds",
                json={"start_ms": 1000, "end_ms": 5000})
    assert client.get(f"/api/sources/{seeded['source_id']}/labels").json() == []


def test_bounds_404s_on_an_unknown_rally(client, seeded):
    r = client.post("/api/rallies/nope/bounds", json={"start_ms": 1, "end_ms": 2})
    assert r.status_code == 404


def test_a_boundary_drag_does_not_overwrite_an_existing_verdict(client, conn, seeded):
    # Two rows for one span: the verdict from label mode and the correction
    # from the drag. latest_labels returns the drag (it is later), and the
    # verdict row is still in the table for the exporter to find.
    rally = _first_rally(conn)
    client.post(f"/api/rallies/{rally['id']}/label",
                json={"verdict": "clean", "boundary_flags": []})
    client.post(f"/api/rallies/{rally['id']}/bounds",
                json={"start_ms": 1400, "end_ms": 4600})

    total = conn.execute("SELECT COUNT(*) FROM rally_labels").fetchone()[0]
    assert total == 2
