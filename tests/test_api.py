import pytest
from fastapi.testclient import TestClient

from bootleg.api.app import create_app
from bootleg.db.rallies import list_rallies, replace_rallies
from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import add_source, find_or_create_session_for_date
from bootleg.detect.segment import Interval


@pytest.fixture
def conn(library):
    c = connect(library.db_path)
    migrate(c)
    return c


@pytest.fixture
def client(library, conn):
    return TestClient(create_app(library))


@pytest.fixture
def seeded(library, conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-19")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-19T10:00:00Z", duration_ms=60_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_0001.MOV",
    )
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7)])
    return {"session_id": session_id, "source_id": source_id, "idx": idx}


def test_list_sessions_includes_counts(client, seeded):
    r = client.get("/api/sessions")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["rally_count"] == 2
    assert body[0]["starred_count"] == 0


def test_get_session_returns_sources_and_rallies(client, seeded):
    r = client.get(f"/api/sessions/{seeded['session_id']}")
    assert r.status_code == 200
    body = r.json()
    assert len(body["sources"]) == 1
    assert len(body["rallies"]) == 2
    assert body["rallies"][0]["idx"] == 1


def test_get_unknown_session_is_404(client):
    assert client.get("/api/sessions/nope").status_code == 404


def test_star_endpoint_updates_the_row(client, conn, seeded):
    rally_id = list_rallies(conn, seeded["session_id"])[0]["id"]
    assert client.post(f"/api/rallies/{rally_id}/star",
                       json={"starred": True}).status_code == 200
    assert list_rallies(conn, seeded["session_id"])[0]["starred"] == 1


def test_bounds_endpoint_does_not_touch_det_columns(client, conn, seeded):
    rally_id = list_rallies(conn, seeded["session_id"])[0]["id"]
    client.post(f"/api/rallies/{rally_id}/bounds",
                json={"start_ms": 1200, "end_ms": 4800})
    row = list_rallies(conn, seeded["session_id"])[0]
    assert (row["start_ms"], row["end_ms"]) == (1200, 4800)
    assert (row["det_start_ms"], row["det_end_ms"]) == (1000, 5000)


def test_bounds_rejects_inverted_range(client, conn, seeded):
    rally_id = list_rallies(conn, seeded["session_id"])[0]["id"]
    r = client.post(f"/api/rallies/{rally_id}/bounds",
                    json={"start_ms": 5000, "end_ms": 1000})
    assert r.status_code == 422


def test_jobs_endpoint_returns_a_list(client):
    r = client.get("/api/jobs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_media_returns_full_body_without_range_header(client, library, seeded):
    src_dir = library.source_dir(seeded["session_id"], seeded["idx"])
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "proxy.mp4").write_bytes(b"0123456789")

    r = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/proxy.mp4")
    assert r.status_code == 200
    assert r.content == b"0123456789"
    assert r.headers["accept-ranges"] == "bytes"


def test_media_serves_partial_content_for_a_range(client, library, seeded):
    src_dir = library.source_dir(seeded["session_id"], seeded["idx"])
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "proxy.mp4").write_bytes(b"0123456789")

    r = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/proxy.mp4",
                   headers={"Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.content == b"2345"
    assert r.headers["content-range"] == "bytes 2-5/10"


def test_media_open_ended_range(client, library, seeded):
    src_dir = library.source_dir(seeded["session_id"], seeded["idx"])
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "proxy.mp4").write_bytes(b"0123456789")

    r = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/proxy.mp4",
                   headers={"Range": "bytes=7-"})
    assert r.status_code == 206
    assert r.content == b"789"


def test_media_unsatisfiable_range_is_416(client, library, seeded):
    src_dir = library.source_dir(seeded["session_id"], seeded["idx"])
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "proxy.mp4").write_bytes(b"0123456789")

    r = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/proxy.mp4",
                   headers={"Range": "bytes=50-60"})
    assert r.status_code == 416


def test_media_missing_file_is_404(client, seeded):
    assert client.get(f"/media/{seeded['session_id']}/9/proxy.mp4").status_code == 404


def test_resegment_rewrites_rallies_from_cached_features(client, library, conn, seeded):
    from bootleg.detect.features import FeatureFrame, Player, write_features

    frames = [
        FeatureFrame(i * 200, 2,
                     Player(0.5, 0.9, 0.30, 2.5), Player(0.5, 0.4, 0.10, 2.5),
                     hits=1, hit_reg=0.9)
        for i in range(40)
    ]
    src_dir = library.source_dir(seeded["session_id"], seeded["idx"])
    src_dir.mkdir(parents=True, exist_ok=True)
    write_features(src_dir / "features.jsonl", frames)

    r = client.post(f"/api/sources/{seeded['source_id']}/resegment",
                    json={"threshold": 0.45})
    assert r.status_code == 200
    assert r.json()["count"] == 1
