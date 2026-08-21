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
    # M2: source_id must ride along so a caller merging labels from several
    # sources of one session can key on (source_id, span) rather than span
    # alone -- two sources' rallies can land on an identical span.
    assert verdict_rows[0]["source_id"] == seeded["source_id"]


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


def test_a_boundary_drags_label_survives_a_failed_bounds_write(client, conn, seeded, monkeypatch):
    # api_bounds records the label before calling set_bounds specifically so
    # that a failure in the second write can never take the correction down
    # with it. Force that failure here and check the label landed anyway,
    # even though the request itself fails and the bounds are left untouched.
    rally = _first_rally(conn)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure between the two commits")

    monkeypatch.setattr("bootleg.api.routes.set_bounds", _boom)

    with pytest.raises(RuntimeError):
        client.post(f"/api/rallies/{rally['id']}/bounds",
                    json={"start_ms": 1400, "end_ms": 4600})

    rows = client.get(f"/api/sources/{seeded['source_id']}/labels").json()
    assert len(rows) == 1
    assert rows[0]["true_start_ms"] == 1400
    assert rows[0]["true_end_ms"] == 4600

    row = conn.execute(
        "SELECT start_ms, end_ms FROM rallies WHERE id = ?", (rally["id"],)
    ).fetchone()
    assert (row["start_ms"], row["end_ms"]) == (1000, 5000)


def test_a_boundary_drag_carries_the_existing_verdict_forward(client, conn, seeded):
    # Judge, then trim. latest_labels returns exactly one row per span (the
    # newest), so the drag's row is the only one any reader -- the exporter,
    # the scorer, LabelController -- will ever see for this span. If that row
    # doesn't carry the verdict forward, the judgement silently vanishes: not
    # "still in the table for the exporter to find" (the old, false claim
    # here), since cmd_labels_export reads latest_labels same as everyone
    # else, not the raw table.
    rally = _first_rally(conn)
    client.post(f"/api/rallies/{rally['id']}/label",
                json={"verdict": "clean", "boundary_flags": []})
    client.post(f"/api/rallies/{rally['id']}/bounds",
                json={"start_ms": 1400, "end_ms": 4600})

    # Append-only still holds -- both writes landed as separate rows.
    total = conn.execute("SELECT COUNT(*) FROM rally_labels").fetchone()[0]
    assert total == 2

    rows = client.get(f"/api/sources/{seeded['source_id']}/labels").json()
    assert len(rows) == 1
    assert rows[0]["verdict"] == "clean"
    assert rows[0]["true_start_ms"] == 1400
    assert rows[0]["true_end_ms"] == 4600


def test_a_label_carries_an_existing_boundary_correction_forward(client, conn, seeded):
    # Mirror case: trim, then judge. Same requirement in the other order --
    # whichever write happens second must not erase the first's half.
    rally = _first_rally(conn)
    client.post(f"/api/rallies/{rally['id']}/bounds",
                json={"start_ms": 1400, "end_ms": 4600})
    client.post(f"/api/rallies/{rally['id']}/label",
                json={"verdict": "clean", "boundary_flags": []})

    total = conn.execute("SELECT COUNT(*) FROM rally_labels").fetchone()[0]
    assert total == 2

    rows = client.get(f"/api/sources/{seeded['source_id']}/labels").json()
    assert len(rows) == 1
    assert rows[0]["verdict"] == "clean"
    assert rows[0]["true_start_ms"] == 1400
    assert rows[0]["true_end_ms"] == 4600


def test_a_label_after_a_drag_recomputes_flags_instead_of_trusting_an_empty_client_list(
    client, conn, seeded
):
    # Finding 1. LabelController (web/src/lib/labels.ts) skips every row
    # whose verdict is NULL when it seeds state, so the drag's row above is
    # invisible in label mode -- the reviewer never saw ['start_early',
    # 'end_late'] to keep or clear. boundary_flags=[] on the label POST is
    # therefore not an explicit choice to clear them; it must not be trusted
    # over what the carried true_start_ms/true_end_ms actually measure.
    rally = _first_rally(conn)
    client.post(f"/api/rallies/{rally['id']}/bounds",
                json={"start_ms": 1400, "end_ms": 4600})
    r = client.post(f"/api/rallies/{rally['id']}/label",
                    json={"verdict": "clean", "boundary_flags": []})
    assert r.status_code == 200

    rows = client.get(f"/api/sources/{seeded['source_id']}/labels").json()
    assert len(rows) == 1
    assert rows[0]["verdict"] == "clean"
    assert rows[0]["true_start_ms"] == 1400
    assert rows[0]["true_end_ms"] == 4600
    assert rows[0]["boundary_flags"] == ["start_early", "end_late"]


def test_a_label_with_a_carried_correction_ignores_a_conflicting_client_flag_list(
    client, conn, seeded
):
    # Same principle, stronger case: once a numeric correction is carried
    # forward, boundary_flags is a pure function of det_* vs true_* -- there
    # is exactly one right answer, so even a non-empty client list must not
    # override it when it disagrees. The client could not have measured this
    # correction (see test above), so its guess is not authoritative.
    rally = _first_rally(conn)
    client.post(f"/api/rallies/{rally['id']}/bounds",
                json={"start_ms": 1400, "end_ms": 4600})
    client.post(f"/api/rallies/{rally['id']}/label",
                json={"verdict": "clean", "boundary_flags": ["start_late"]})

    rows = client.get(f"/api/sources/{seeded['source_id']}/labels").json()
    assert len(rows) == 1
    assert rows[0]["boundary_flags"] == ["start_early", "end_late"]


def test_a_label_with_no_carried_correction_still_honours_explicit_client_flags(
    client, conn, seeded
):
    # The other half of the same decision: with nothing carried forward to
    # measure against, the client's flags are the reviewer's own live,
    # explicit judgement and must still be written as sent.
    rally = _first_rally(conn)
    r = client.post(f"/api/rallies/{rally['id']}/label",
                    json={"verdict": "partly", "boundary_flags": ["start_early"]})
    assert r.status_code == 200

    rows = client.get(f"/api/sources/{seeded['source_id']}/labels").json()
    assert rows[0]["boundary_flags"] == ["start_early"]
