import pytest
from fastapi.testclient import TestClient

from bootleg.api.app import create_app
from bootleg.db.rallies import replace_rallies, set_point, set_star
from bootleg.db.reels import create_reel, get_reel_by_slug, mark_rendered
from bootleg.db.sessions import add_source, find_or_create_session_for_date
from bootleg.detect.segment import Interval
from bootleg.media.clips import clip_relpath


@pytest.fixture
def client(library):
    with TestClient(create_app(library)) as c:
        yield c


@pytest.fixture
def session(conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id, [
        Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.6), Interval(20000, 24000, 0.7),
    ])
    rallies = conn.execute("SELECT * FROM rallies ORDER BY idx").fetchall()
    set_point(conn, rallies[0]["id"], True)
    set_point(conn, rallies[1]["id"], True)
    set_star(conn, rallies[1]["id"], True)
    return {
        "id": session_id, "source_id": source_id, "idx": idx,
        "rallies": [dict(r) for r in rallies],
    }


def _span(session, start, end):
    return {"source_id": session["source_id"], "start_ms": start, "end_ms": end}


def test_create_and_list_a_reel(client):
    created = client.post("/api/reels", json={"name": "Best of July"}).json()
    assert created["slug"] == "best-of-july"
    assert created["item_count"] == 0

    listed = client.get("/api/reels").json()
    assert [r["slug"] for r in listed] == ["best-of-july"]


def test_creating_a_reel_with_a_blank_name_is_refused(client):
    assert client.post("/api/reels", json={"name": "   "}).status_code == 422


def test_get_a_reel_resolves_its_items(client, conn, session, library):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    client.post(f"/api/reels/{reel['slug']}/items",
                json={"items": [_span(session, 1000, 5000)]})

    body = client.get(f"/api/reels/{reel['slug']}").json()

    assert body["reel"]["slug"] == reel["slug"]
    # Same shape as a listed reel: the frontend shares one type across
    # /api/reels and this route, so a missing item_count would read as
    # undefined at runtime while the type promised a number.
    assert body["reel"]["item_count"] == 1
    item = body["items"][0]
    # Everything the builder row and the preview need, in one response.
    assert item["session_id"] == session["id"]
    assert item["source_idx"] == session["idx"]
    assert item["duration_ms"] == 4000
    assert item["clip_ready"] is False
    assert item["rally"]["idx"] == 1


def test_get_an_unknown_reel_is_404(client):
    assert client.get("/api/reels/nope").status_code == 404


def test_adding_items_is_additive(client, session):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    slug = reel["slug"]
    client.post(f"/api/reels/{slug}/items", json={"items": [_span(session, 1000, 5000)]})

    body = client.post(f"/api/reels/{slug}/items", json={"items": [
        _span(session, 1000, 5000), _span(session, 9000, 14000),
    ]}).json()

    assert body == {"added": 1, "existing": 1, "total": 2}


def test_adding_an_item_marks_the_reel_dirty(client, conn, session):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    conn.execute("UPDATE reels SET dirty = 0 WHERE slug = ?", (reel["slug"],))
    conn.commit()

    client.post(f"/api/reels/{reel['slug']}/items",
                json={"items": [_span(session, 1000, 5000)]})

    assert client.get(f"/api/reels/{reel['slug']}").json()["reel"]["dirty"] == 1


def test_removing_an_item(client, session):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    slug = reel["slug"]
    client.post(f"/api/reels/{slug}/items", json={"items": [
        _span(session, 1000, 5000), _span(session, 9000, 14000),
    ]})

    body = client.post(f"/api/reels/{slug}/items/remove",
                       json=_span(session, 1000, 5000)).json()

    assert body == {"removed": True, "total": 1}
    assert client.post(f"/api/reels/{slug}/items/remove",
                       json=_span(session, 1000, 5000)).json()["removed"] is False


def test_reordering(client, session):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    slug = reel["slug"]
    client.post(f"/api/reels/{slug}/items", json={"items": [
        _span(session, 1000, 5000), _span(session, 9000, 14000),
    ]})

    res = client.post(f"/api/reels/{slug}/order", json={"order": [
        _span(session, 9000, 14000), _span(session, 1000, 5000),
    ]})

    assert res.status_code == 200
    items = client.get(f"/api/reels/{slug}").json()["items"]
    assert [i["start_ms"] for i in items] == [9000, 1000]


def test_a_reorder_that_is_not_the_membership_is_refused(client, session):
    # A stale client list racing a removal. 409, not 500: the client's view
    # is out of date, and refetching is the fix.
    reel = client.post("/api/reels", json={"name": "r"}).json()
    slug = reel["slug"]
    client.post(f"/api/reels/{slug}/items", json={"items": [
        _span(session, 1000, 5000), _span(session, 9000, 14000),
    ]})

    res = client.post(f"/api/reels/{slug}/order",
                      json={"order": [_span(session, 1000, 5000)]})

    assert res.status_code == 409


def test_reel_export_queues_clip_jobs(client, conn, session):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    client.post(f"/api/reels/{reel['slug']}/items", json={"items": [
        _span(session, 1000, 5000), _span(session, 9000, 14000),
    ]})

    body = client.post(f"/api/reels/{reel['slug']}/export").json()

    assert body == {"queued": 2, "already_cut": 0, "in_flight": 0,
                    "unavailable": 0, "total": 2}
    types = [r["type"] for r in conn.execute("SELECT type FROM jobs")]
    assert types == ["clip", "clip"]


def test_a_second_export_reports_in_flight_not_already_cut(client, session):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    client.post(f"/api/reels/{reel['slug']}/items",
                json={"items": [_span(session, 1000, 5000)]})
    client.post(f"/api/reels/{reel['slug']}/export")

    body = client.post(f"/api/reels/{reel['slug']}/export").json()

    assert body["queued"] == 0
    assert body["in_flight"] == 1
    assert body["already_cut"] == 0


def test_render_refuses_while_clips_are_missing(client, conn, session):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    client.post(f"/api/reels/{reel['slug']}/items", json={"items": [
        _span(session, 1000, 5000), _span(session, 9000, 14000),
    ]})

    res = client.post(f"/api/reels/{reel['slug']}/render")

    assert res.status_code == 409
    # The count is named, so the UI can say WHY without a second request.
    assert "2" in res.json()["detail"]
    assert conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 0


def test_render_enqueues_once_clips_exist(client, conn, session, library):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    client.post(f"/api/reels/{reel['slug']}/items",
                json={"items": [_span(session, 1000, 5000)]})
    clips = library.clips_dir(session["id"])
    clips.mkdir(parents=True, exist_ok=True)
    (clips / clip_relpath(session["idx"], 1000, 5000)).write_bytes(b"fake")

    body = client.post(f"/api/reels/{reel['slug']}/render").json()

    assert body["job_id"]
    row = conn.execute("SELECT type, payload FROM jobs").fetchone()
    assert row["type"] == "reel"


def test_render_refuses_an_empty_reel(client):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    assert client.post(f"/api/reels/{reel['slug']}/render").status_code == 409


def test_a_second_render_does_not_queue_twice(client, conn, session, library):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    client.post(f"/api/reels/{reel['slug']}/items",
                json={"items": [_span(session, 1000, 5000)]})
    clips = library.clips_dir(session["id"])
    clips.mkdir(parents=True, exist_ok=True)
    (clips / clip_relpath(session["idx"], 1000, 5000)).write_bytes(b"fake")

    first = client.post(f"/api/reels/{reel['slug']}/render").json()
    second = client.post(f"/api/reels/{reel['slug']}/render").json()

    assert first["job_id"] == second["job_id"]
    assert conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 1


def test_session_reel_creates_from_the_point_set(client, session):
    body = client.post(f"/api/sessions/{session['id']}/reels",
                       json={"which": "points"}).json()

    assert body["name"] == "2026-08-18 points"
    assert body["slug"] == "2026-08-18-points"
    assert body["added"] == 2

    items = client.get("/api/reels/2026-08-18-points").json()["items"]
    # Chronological, which for one source is start order.
    assert [i["start_ms"] for i in items] == [1000, 9000]


def test_session_reel_creates_from_the_starred_set(client, session):
    body = client.post(f"/api/sessions/{session['id']}/reels",
                       json={"which": "starred"}).json()
    assert body["slug"] == "2026-08-18-starred"
    assert body["added"] == 1


def test_a_second_click_merges_into_the_same_reel(client, conn, session):
    first = client.post(f"/api/sessions/{session['id']}/reels",
                        json={"which": "points"}).json()
    slug = first["slug"]
    # Reorder by hand, then mark a third rally a point and click again.
    client.post(f"/api/reels/{slug}/order", json={"order": [
        _span(session, 9000, 14000), _span(session, 1000, 5000),
    ]})
    set_point(conn, session["rallies"][2]["id"], True)

    second = client.post(f"/api/sessions/{session['id']}/reels",
                         json={"which": "points"}).json()

    assert second["slug"] == slug
    assert (second["added"], second["existing"]) == (1, 2)
    items = client.get(f"/api/reels/{slug}").json()["items"]
    # The hand-ordering survives and the new span lands at the end. Anything
    # else silently discards a manual reorder.
    assert [i["start_ms"] for i in items] == [9000, 1000, 20000]


def test_a_session_reel_skips_rejected_rallies(client, conn, session):
    conn.execute("UPDATE rallies SET rejected = 1 WHERE start_ms = 1000")
    conn.commit()
    body = client.post(f"/api/sessions/{session['id']}/reels",
                       json={"which": "points"}).json()
    assert body["added"] == 1


def test_a_session_reel_with_a_bad_set_is_refused(client, session):
    res = client.post(f"/api/sessions/{session['id']}/reels", json={"which": "all"})
    assert res.status_code == 422


def test_a_session_reel_for_an_unknown_session_is_404(client):
    res = client.post("/api/sessions/nope/reels", json={"which": "points"})
    assert res.status_code == 404


def test_a_generated_slug_yields_to_one_already_taken(client, conn, session):
    # A hand-made reel took the slug first. The generated reel must not
    # collide, and must not silently merge into a reel it did not create --
    # it merges by NAME, and this one's name is different.
    create_reel(conn, "2026-08-18 Points")
    assert get_reel_by_slug(conn, "2026-08-18-points") is not None

    body = client.post(f"/api/sessions/{session['id']}/reels",
                       json={"which": "points"}).json()

    assert body["slug"] == "2026-08-18-points-2"


def _rendered_reel(conn, library, name, data=b"0123456789fake-mp4-bytes"):
    """A reel whose `rendered_path` points at a real file on disk, the way
    `handle_reel` leaves one after a successful render."""
    reel = create_reel(conn, name)
    rel = f"reels/{reel['slug']}.mp4"
    dst = library.root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    mark_rendered(conn, reel["id"], rel, set())
    return reel, dst


def test_reel_media_serves_the_rendered_file(client, conn, library):
    reel, _dst = _rendered_reel(conn, library, "r")

    res = client.get(f"/media/reels/{reel['slug']}.mp4")

    assert res.status_code == 200
    assert res.headers["accept-ranges"] == "bytes"
    assert res.content == b"0123456789fake-mp4-bytes"


def test_reel_media_honours_a_range_header(client, conn, library):
    reel, _dst = _rendered_reel(conn, library, "r")

    res = client.get(f"/media/reels/{reel['slug']}.mp4", headers={"Range": "bytes=0-3"})

    assert res.status_code == 206
    assert res.content == b"0123"
    assert res.headers["content-range"] == "bytes 0-3/24"


def test_reel_media_404s_for_an_unknown_slug(client):
    assert client.get("/media/reels/nope.mp4").status_code == 404


def test_reel_media_404s_for_a_reel_that_was_never_rendered(client, conn):
    reel = create_reel(conn, "never rendered")
    assert client.get(f"/media/reels/{reel['slug']}.mp4").status_code == 404


def test_reel_media_404s_when_the_file_has_since_been_deleted(client, conn, library):
    reel, dst = _rendered_reel(conn, library, "r")
    dst.unlink()

    assert client.get(f"/media/reels/{reel['slug']}.mp4").status_code == 404


def test_reel_media_is_still_servable_while_the_reel_is_dirty(client, conn, library):
    # mark_rendered leaves rendered_path set even when a later add/remove
    # marks the reel dirty again -- the file on disk is still the last
    # successful render and still watchable, just possibly stale membership.
    reel, _dst = _rendered_reel(conn, library, "r")
    conn.execute("UPDATE reels SET dirty = 1 WHERE id = ?", (reel["id"],))
    conn.commit()

    assert client.get(f"/media/reels/{reel['slug']}.mp4").status_code == 200
