"""The span-addressed half of labelling: windows the detector never proposed.

`/api/rallies/{id}/label` can only judge footage that became a rally, which
is why the corpus has never been able to measure recall. These two routes take
a span directly.
"""

import pytest
from fastapi.testclient import TestClient

from splitstep.api.app import create_app
from splitstep.db.labels import latest_labels
from splitstep.db.rallies import replace_rallies
from splitstep.db.sessions import add_source, find_or_create_session_for_date
from splitstep.detect.segment import Interval


@pytest.fixture
def client(library, conn):
    with TestClient(create_app(library)) as c:
        yield c


@pytest.fixture
def seeded(library, conn):
    session_id = find_or_create_session_for_date(conn, "2026-09-16")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-09-16T10:00:00Z", duration_ms=600_000,
        width=1920, height=1080, fps=30.0, original_name="IMG_0001.MOV",
    )
    replace_rallies(conn, session_id, source_id,
                    [Interval(60_000, 70_000, 0.8), Interval(200_000, 214_000, 0.7)])
    return {"session_id": session_id, "source_id": source_id, "idx": idx}


# -- the sample -------------------------------------------------------------


def test_the_sample_returns_the_requested_number_of_windows(client, seeded):
    r = client.get(f"/api/sources/{seeded['source_id']}/label-sample?n=8&seed=1")
    assert r.status_code == 200
    assert len(r.json()["windows"]) == 8


def test_the_same_seed_returns_the_same_sample(client, seeded):
    url = f"/api/sources/{seeded['source_id']}/label-sample?n=8&seed=1"
    assert client.get(url).json() == client.get(url).json()


def test_the_sample_echoes_the_seed_it_used(client, seeded):
    # The client has to be able to ask for this exact sample again after a
    # reload -- that is what stands in for storing it.
    body = client.get(f"/api/sources/{seeded['source_id']}/label-sample?n=4&seed=42").json()
    assert body["seed"] == 42


def test_a_window_carries_only_its_span(client, seeded):
    # Blindness is the point: anything here saying which windows the detector
    # flagged would re-anchor the reviewer to the thing being measured.
    body = client.get(f"/api/sources/{seeded['source_id']}/label-sample?n=4&seed=1").json()
    assert all(set(w) == {"start_ms", "end_ms"} for w in body["windows"])


def test_the_sample_covers_footage_the_detector_ignored(client, seeded):
    body = client.get(f"/api/sources/{seeded['source_id']}/label-sample?n=10&seed=1").json()
    rallies = [(60_000, 70_000), (200_000, 214_000)]
    ignored = [
        w for w in body["windows"]
        if not any(w["start_ms"] < e and s < w["end_ms"] for s, e in rallies)
    ]
    assert ignored


def test_the_sample_404s_on_an_unknown_source(client, seeded):
    assert client.get("/api/sources/nope/label-sample?n=4&seed=1").status_code == 404


# -- labelling a sampled window --------------------------------------------


def test_labelling_a_sampled_window_lands_in_the_corpus(client, conn, seeded):
    r = client.post(
        f"/api/sources/{seeded['source_id']}/label",
        json={"span_start_ms": 25_000, "span_end_ms": 33_000, "verdict": "clean"},
    )
    assert r.status_code == 200

    rows = latest_labels(conn, seeded["source_id"])
    assert [(x["span_start_ms"], x["span_end_ms"], x["verdict"]) for x in rows] == [
        (25_000, 33_000, "clean")
    ]


def test_a_sampled_label_records_no_rally(client, conn, seeded):
    # There is no rally: that is the whole reason this route exists, and
    # rally_id is provenance only, so NULL is the honest value rather than a
    # fabricated link.
    client.post(
        f"/api/sources/{seeded['source_id']}/label",
        json={"span_start_ms": 25_000, "span_end_ms": 33_000, "verdict": "not_play"},
    )
    row = conn.execute(
        "SELECT rally_id FROM rally_labels WHERE source_id = ?", (seeded["source_id"],)
    ).fetchone()
    assert row["rally_id"] is None


def test_relabelling_a_sampled_window_supersedes_rather_than_erases(client, conn, seeded):
    body = {"span_start_ms": 25_000, "span_end_ms": 33_000}
    client.post(f"/api/sources/{seeded['source_id']}/label", json={**body, "verdict": "clean"})
    client.post(f"/api/sources/{seeded['source_id']}/label", json={**body, "verdict": "not_play"})

    rows = latest_labels(conn, seeded["source_id"])
    assert [x["verdict"] for x in rows] == ["not_play"]
    total = conn.execute("SELECT COUNT(*) c FROM rally_labels").fetchone()["c"]
    assert total == 2


def test_retracting_a_sampled_window_leaves_it_as_unjudged_as_one_nobody_opened(
    client, conn, seeded
):
    body = {"span_start_ms": 25_000, "span_end_ms": 33_000}
    client.post(f"/api/sources/{seeded['source_id']}/label", json={**body, "verdict": "clean"})
    r = client.post(f"/api/sources/{seeded['source_id']}/label/retract", json=body)
    assert r.status_code == 200
    assert latest_labels(conn, seeded["source_id"]) == []


def test_labelling_404s_on_an_unknown_source(client, seeded):
    r = client.post(
        "/api/sources/nope/label",
        json={"span_start_ms": 0, "span_end_ms": 8000, "verdict": "clean"},
    )
    assert r.status_code == 404


def test_labelling_refuses_an_unknown_verdict(client, seeded):
    r = client.post(
        f"/api/sources/{seeded['source_id']}/label",
        json={"span_start_ms": 0, "span_end_ms": 8000, "verdict": "maybe"},
    )
    assert r.status_code == 422


def test_labelling_refuses_an_empty_span(client, seeded):
    # A zero-length span judges no footage, and `latest_labels` resolves per
    # exact span -- one would sit in the corpus forever, matching nothing.
    r = client.post(
        f"/api/sources/{seeded['source_id']}/label",
        json={"span_start_ms": 8000, "span_end_ms": 8000, "verdict": "clean"},
    )
    assert r.status_code == 422
