import os
import platform
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitstep.api.app import create_app
from splitstep.api.routes import _evict_old_frames
from splitstep.db import jobs as jobq
from splitstep.db.presets import create_preset
from splitstep.db.rallies import list_rallies, replace_rallies, set_point, set_rejected, set_star
from splitstep.db.schema import connect
from splitstep.db.sessions import add_source, find_or_create_session_for_date
from splitstep.detect.geometry import Quad
from splitstep.detect.segment import Interval
from splitstep.media.clips import clip_relpath
from splitstep.media.transcode import TranscodeError


@pytest.fixture
def client(library, conn):
    # A plain `with` block, not a bare TestClient(...): lifespan startup and
    # shutdown only run inside the context manager, and shutdown is what
    # closes the per-thread connection pool -- see Finding 7.
    with TestClient(create_app(library)) as c:
        yield c


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


def test_list_sessions_reports_point_count(client, conn, seeded):
    # Points and stars are independent flags (a point need not be starred),
    # so a session can carry points with zero stars -- the case the Library
    # card exists to surface. A rejected rally is not a rally at all, so it
    # must not inflate point_count any more than it inflates rally_count or
    # starred_count; both live rows here are marked as points, but only one
    # survives the rejected=0 filter.
    rallies = list_rallies(conn, seeded["session_id"])
    set_point(conn, rallies[0]["id"], True)
    set_point(conn, rallies[1]["id"], True)
    set_rejected(conn, rallies[1]["id"], True)

    r = client.get("/api/sessions")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["point_count"] == 1
    assert body[0]["starred_count"] == 0
    assert body[0]["rally_count"] == 1


def test_list_sessions_names_a_source_to_take_a_thumbnail_from(client, seeded):
    # The library card wants a still, and /media/{session}/{idx}/frame.jpg
    # can produce one -- but the list had no idx to build that URL from, and
    # the client guessing 1 would be wrong for any session whose first
    # source was never added or was removed. The lowest idx is the one whose
    # footage opens the session.
    body = client.get("/api/sessions").json()
    assert body[0]["thumb_idx"] == seeded["idx"]


def test_list_sessions_thumb_idx_is_the_lowest_source_not_the_newest(client, conn, seeded):
    add_source(
        conn, seeded["session_id"], recorded_at="2026-08-19T12:00:00Z",
        duration_ms=60_000, width=3840, height=2160, fps=30.0,
        original_name="IMG_0002.MOV",
    )
    body = client.get("/api/sessions").json()
    assert body[0]["thumb_idx"] == 1


def test_list_sessions_thumb_idx_is_null_with_no_sources(client, conn):
    find_or_create_session_for_date(conn, "2026-08-20")
    body = client.get("/api/sessions").json()
    empty = [s for s in body if s["id"] != "2026-08-19"]
    assert len(empty) == 1
    # Not 1-with-a-hope: the client renders a placeholder for null, whereas
    # a guessed idx would render a broken image request against a source
    # that does not exist.
    assert empty[0]["thumb_idx"] is None


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


def test_preset_endpoint_assigns_the_preset_to_the_source(client, conn, seeded):
    quad = Quad(((0.1, 0.9), (0.9, 0.9), (0.7, 0.3), (0.3, 0.3)))
    preset_id = create_preset(conn, "backyard", quad)

    r = client.post(f"/api/sources/{seeded['source_id']}/preset",
                    json={"preset_id": preset_id})
    assert r.status_code == 200
    row = conn.execute(
        "SELECT court_preset_id FROM sources WHERE id = ?", (seeded["source_id"],)
    ).fetchone()
    assert row["court_preset_id"] == preset_id


def test_preset_endpoint_unknown_source_is_404(client, conn):
    preset_id = create_preset(conn, "backyard",
                              Quad(((0.1, 0.9), (0.9, 0.9), (0.7, 0.3), (0.3, 0.3))))
    r = client.post("/api/sources/no-such-source/preset", json={"preset_id": preset_id})
    assert r.status_code == 404


def test_preset_endpoint_unknown_preset_is_404(client, seeded):
    r = client.post(f"/api/sources/{seeded['source_id']}/preset",
                    json={"preset_id": "no-such-preset"})
    assert r.status_code == 404


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
    from splitstep.detect.features import FeatureFrame, Player, write_features

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


def test_scores_with_no_threshold_returns_the_resolved_profile_default(
    client, library, seeded, ground_features
):
    """No `threshold` in the query string means "use the profile's default",
    and api_scores must echo back what params_for_frames actually resolved
    (params.threshold) rather than the raw None it was called with -- that
    resolved value is what lets the UI adopt the right default on first load.

    ground_features is real footage that classifies as subject mode
    (threshold 0.25), deliberately not pair mode (0.45). Pair's default
    happens to equal SegmentParams()'s hardcoded dataclass default, so a
    regression that returned that hardcoded default without ever running
    classification would still pass a pair-fixture assertion; subject's 0.25
    only comes out if params_for_frames's resolution actually ran.
    """
    from splitstep.detect.features import write_features

    src_dir = library.source_dir(seeded["session_id"], seeded["idx"])
    src_dir.mkdir(parents=True, exist_ok=True)
    write_features(src_dir / "features.jsonl", ground_features)

    r = client.get(f"/api/sources/{seeded['source_id']}/scores")
    assert r.status_code == 200
    body = r.json()
    assert body["threshold"] == 0.25
    assert body["step_ms"] == 200
    assert len(body["scores"]) == len(ground_features)


def test_scores_with_explicit_threshold_returns_it_unchanged(client, library, seeded):
    """The re-segment slider passes an explicit threshold to override the
    profile default -- that value must come back verbatim, same as
    params_for_frames itself (see test_params_for_frames_honours_an_explicit_threshold)."""
    from splitstep.detect.features import FeatureFrame, Player, write_features

    frames = [
        FeatureFrame(i * 200, 2,
                     Player(0.5, 0.9, 0.30, 2.5), Player(0.5, 0.4, 0.10, 2.5),
                     hits=1, hit_reg=0.9)
        for i in range(40)
    ]
    src_dir = library.source_dir(seeded["session_id"], seeded["idx"])
    src_dir.mkdir(parents=True, exist_ok=True)
    write_features(src_dir / "features.jsonl", frames)

    r = client.get(f"/api/sources/{seeded['source_id']}/scores", params={"threshold": 0.6})
    assert r.status_code == 200
    body = r.json()
    assert body["threshold"] == 0.6
    assert body["step_ms"] == 200
    assert len(body["scores"]) == len(frames)


# -- Finding 7: each request gets its own sqlite connection -----------------

def test_two_concurrent_requests_do_not_share_a_connection(library):
    """Every route is `def`, not `async def`, so Starlette runs each one on
    an anyio worker thread. A single `app.state.conn` shared by every thread
    is what let a concurrent `set_star` commit() finalize a `replace_rallies`
    transaction another thread had open and had not committed yet. Getting
    the connection the same way a route does -- `app.state.conns.get()` --
    from two different threads must return two different connection objects.
    """
    app = create_app(library)
    barrier = threading.Barrier(2)
    conns: dict[str, object] = {}

    def hit(name: str) -> None:
        barrier.wait()
        conns[name] = app.state.conns.get()

    t1 = threading.Thread(target=hit, args=("a",))
    t2 = threading.Thread(target=hit, args=("b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert conns["a"] is not conns["b"]
    # Same thread, called again, reuses its own connection rather than
    # opening a new one on every call.
    assert app.state.conns.get() is app.state.conns.get()
    app.state.conns.close_all()


def test_connections_are_closed_on_app_shutdown(library):
    """The pool must not leak connections -- close_all() (wired to the app's
    lifespan shutdown) must leave every connection it handed out unusable.
    """
    app = create_app(library)
    conn = app.state.conns.get()
    app.state.conns.close_all()

    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")

    # And the pool recovers: a later .get() opens a fresh, working connection.
    fresh = app.state.conns.get()
    fresh.execute("SELECT 1")
    app.state.conns.close_all()


def test_sharing_one_connection_across_callers_finalizes_an_unrelated_open_transaction(
    library, conn
):
    """Documents the actual mechanism Finding 7 describes: a sqlite3
    transaction lives on the Connection *object*, not on the calling
    thread. On the old design (one `app.state.conn` shared by every route),
    `replace_rallies` holding an explicit `BEGIN IMMEDIATE` mid-rewrite and
    a concurrent `set_star` call on that *same* connection object would
    have set_star's `commit()` finalize replace_rallies's half-applied
    write too -- there is only one transaction, because there is only one
    connection. This is exactly the hazard request-scoped connections
    (proven distinct in test_two_concurrent_requests_do_not_share_a_connection
    above) eliminate structurally.
    """
    session_id = find_or_create_session_for_date(conn, "2026-08-19")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-19T10:00:00Z", duration_ms=10_000,
        width=1920, height=1080, fps=30.0, original_name="a.mov",
    )
    conn.commit()

    shared = connect(library.db_path)  # stands in for the old app.state.conn

    # Something like replace_rallies: an explicit transaction with a write
    # that has not been committed yet.
    shared.execute("BEGIN IMMEDIATE")
    shared.execute(
        "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
        "det_start_ms,det_end_ms,confidence) VALUES (?,?,?,?,?,?,?,?,?)",
        ("mid-flight", session_id, source_id, 999, 0, 100, 0, 100, 0.5),
    )

    # An unrelated set_star call on the *same shared connection* -- what
    # every route did before this fix.
    set_star(shared, "does-not-exist", True)

    # set_star's commit() finalized the still-in-flight insert too: this is
    # the bug. A different connection sees it as durably committed.
    other = connect(library.db_path)
    try:
        assert other.execute(
            "SELECT COUNT(*) AS n FROM rallies WHERE id = 'mid-flight'"
        ).fetchone()["n"] == 1
    finally:
        other.close()
        shared.close()


# -- preview.jpg: setup-wizard frames from the original, before a proxy exists

def test_preview_serves_a_frame_from_the_original(client, registered_source):
    r = client.get(f"/media/{registered_source.session_id}/1/preview.jpg?at_ms=500&rot=0")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"


def test_preview_caches_per_rotation(client, registered_source, tmp_path):
    base = f"/media/{registered_source.session_id}/1/preview.jpg?at_ms=500"
    client.get(f"{base}&rot=0")
    client.get(f"{base}&rot=90")
    names = {p.name for p in registered_source.dir.glob("preview-*.jpg")}
    assert names == {"preview-0-500.jpg", "preview-90-500.jpg"}


def test_preview_rejects_a_non_right_angle(client, registered_source):
    r = client.get(f"/media/{registered_source.session_id}/1/preview.jpg?at_ms=0&rot=45")
    assert r.status_code == 400


def test_preview_clamps_past_the_end_of_the_clip(client, registered_source):
    r = client.get(f"/media/{registered_source.session_id}/1/preview.jpg?at_ms=99999999&rot=0")
    assert r.status_code == 200


def test_preview_404s_when_the_original_is_gone(client, registered_source):
    for p in registered_source.dir.glob("original.*"):
        p.unlink()
    r = client.get(f"/media/{registered_source.session_id}/1/preview.jpg?at_ms=0&rot=0")
    assert r.status_code == 404


def test_preview_write_is_atomic_on_extraction_failure(
    client, registered_source, monkeypatch
):
    """A failed/interrupted extraction must not leave a torn file at the
    cache path a concurrent reader could be served mid-write. Simulate an
    ffmpeg that partially writes its output path and then dies: the
    partial bytes must land on a temp path, never on `dst` itself, so
    `preview-*.jpg` must be empty afterwards -- not a half-written JPEG.
    """
    def _dies_after_partial_write(src, dst, at_ms=0, rotation_deg=0, hwaccel=None):
        Path(dst).write_bytes(b"not a complete jpeg")
        raise TranscodeError("ffmpeg died mid-write")

    monkeypatch.setattr("splitstep.api.routes.extract_frame", _dies_after_partial_write)

    r = client.get(f"/media/{registered_source.session_id}/1/preview.jpg?at_ms=500&rot=0")
    assert r.status_code == 409
    assert list(registered_source.dir.glob("preview-*.jpg")) == []
    # The temp path it wrote to must also be cleaned up, not just renamed
    # away from: only the original ingest left in place, nothing else.
    remaining = {p.name for p in registered_source.dir.iterdir()}
    assert all(name.startswith("original.") for name in remaining)


def test_preview_leaked_temp_file_is_eventually_swept(client, registered_source, monkeypatch):
    """A process killed between extract_frame finishing and api_preview's
    `finally: tmp.unlink()` actually running leaks the temp file it wrote
    to -- `finally` runs on any ordinary exception, so only a real crash
    (never reached, by definition, in a single test process) skips it.
    Stand in for that by making cleanup itself a no-op for the duration of
    one request, the same end state a SIGKILL leaves behind: the temp file
    the ROUTE'S OWN CODE named stays on disk.

    Before this fix that name was a dotfile (`.preview-....jpg`), which the
    "preview-*.jpg" glob `_evict_old_frames` sweeps after every request
    never matches -- so it stayed on disk forever, no matter how many later
    requests ran. Renaming it to start with "preview-" makes it ordinary
    eviction fodder: once enough fresher files exist to push it out of the
    "keep most recent" window, the very same sweep every preview request
    already triggers reclaims it.
    """
    captured: dict[str, Path] = {}

    def dies_after_writing(src, dst, at_ms=0, rotation_deg=0, hwaccel=None):
        Path(dst).write_bytes(b"leaked mid-extraction")
        captured["tmp"] = Path(dst)
        # Anything other than TranscodeError/ProbeError/FileNotFoundError:
        # api_preview does not catch it, so it propagates out uncaught --
        # the same "nothing ran to completion" shape as a real crash.
        raise RuntimeError("process killed mid-extraction")

    real_unlink = Path.unlink
    monkeypatch.setattr("splitstep.api.routes.extract_frame", dies_after_writing)
    monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=False: None)

    with pytest.raises(RuntimeError):
        client.get(f"/media/{registered_source.session_id}/1/preview.jpg?at_ms=500&rot=0")

    # Restore real cleanup for what the test does next -- only the ROUTE's
    # own cleanup needed to be suppressed, to leave its temp file behind.
    monkeypatch.setattr(Path, "unlink", real_unlink)

    tmp = captured["tmp"]
    assert tmp.exists()
    # The fix itself: a dotfile name can never match "preview-*.jpg", no
    # matter how eviction is tuned.
    assert tmp.name.startswith("preview-")

    old = time.time() - 3600
    os.utime(tmp, (old, old))
    # Enough fresher decoys that the leaked file is no longer among the
    # `keep` most recently touched -- regardless of what the real budget is
    # tuned to (see FRAME_CACHE_KEEP / PREVIEW_CACHE_KEEP).
    for i in range(25):
        (registered_source.dir / f"preview-0-{600 + i}.jpg").write_bytes(b"x")

    _evict_old_frames(registered_source.dir, pattern="preview-*.jpg", keep=20)

    assert not tmp.exists()


def test_preview_rotation_reaches_the_pixels(client, registered_source, tmp_path):
    """An api_preview that built the preview-{rot}-{at_ms}.jpg filename
    correctly but forgot to pass rotation_deg=rot to extract_frame would
    pass every other route test here -- decode the actual pixels via
    ffprobe and confirm rot=90 produces a transposed frame relative to
    rot=0 for the same timestamp.
    """
    base = f"/media/{registered_source.session_id}/1/preview.jpg?at_ms=500"
    r0 = client.get(f"{base}&rot=0")
    r90 = client.get(f"{base}&rot=90")
    assert r0.status_code == 200
    assert r90.status_code == 200

    def _dims(data: bytes, name: str) -> tuple[int, int]:
        path = tmp_path / name
        path.write_bytes(data)
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        w, h = out.split("x")
        return int(w), int(h)

    w0, h0 = _dims(r0.content, "r0.jpg")
    w90, h90 = _dims(r90.content, "r90.jpg")
    # rot=0 keeps the source's 4:3 landscape aspect (w0 > h0); rot=90
    # transposes it to portrait (h90 > w90), and the two aspect ratios are
    # reciprocals of each other -- a small tolerance absorbs scale's -2
    # rounding to the nearest even pixel.
    assert w0 > h0
    assert h90 > w90
    assert w0 / h0 == pytest.approx(h90 / w90, rel=0.02)


def test_get_source_returns_the_row(client, registered_source):
    r = client.get(f"/api/sources/{registered_source.id}")
    assert r.status_code == 200
    assert r.json()["status"] == "needs_setup"
    assert r.json()["rotation_deg"] in (0, 90, 180, 270)


def test_get_source_404s_for_an_unknown_id(client):
    assert client.get("/api/sources/nope").status_code == 404


def test_setup_stores_both_and_queues_a_build(client, registered_source, a_preset):
    r = client.post(
        f"/api/sources/{registered_source.id}/setup",
        json={"rotation_deg": 90, "preset_id": a_preset},
    )
    assert r.status_code == 200
    assert r.json()["job_id"]
    assert client.get(f"/api/sources/{registered_source.id}").json()["rotation_deg"] == 90


def test_setup_rejects_a_non_right_angle(client, registered_source, a_preset):
    r = client.post(
        f"/api/sources/{registered_source.id}/setup",
        json={"rotation_deg": 45, "preset_id": a_preset},
    )
    assert r.status_code == 400


def test_setup_404s_on_an_unknown_preset(client, registered_source):
    r = client.post(
        f"/api/sources/{registered_source.id}/setup",
        json={"rotation_deg": 0, "preset_id": "nope"},
    )
    assert r.status_code == 404


def test_setup_409s_while_a_job_is_running(client, registered_source, a_preset, conn):
    conn.execute(
        "UPDATE sources SET status='detecting' WHERE id=?", (registered_source.id,)
    )
    conn.commit()
    r = client.post(
        f"/api/sources/{registered_source.id}/setup",
        json={"rotation_deg": 0, "preset_id": a_preset},
    )
    assert r.status_code == 409


def test_note_route_writes_the_note(client, conn, seeded):
    rally_id = list_rallies(conn, seeded["session_id"])[0]["id"]

    r = client.post(f"/api/rallies/{rally_id}/note", json={"note": "  late on the backhand  "})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    # Trimmed at the boundary, so what the exporter renders is what the
    # reviewer meant -- trailing spaces would silently widen the caption pill.
    assert list_rallies(conn, seeded["session_id"])[0]["note"] == "late on the backhand"


def test_note_route_refuses_an_over_long_note(client, conn, seeded):
    rally_id = list_rallies(conn, seeded["session_id"])[0]["id"]

    r = client.post(f"/api/rallies/{rally_id}/note", json={"note": "x" * 121})
    # 422, the same shape every other pydantic validator in this router
    # produces -- the UI is not the only writer a library ever has.
    assert r.status_code == 422
    assert list_rallies(conn, seeded["session_id"])[0]["note"] == ""


def test_note_route_accepts_a_note_at_exactly_the_cap(client, conn, seeded):
    rally_id = list_rallies(conn, seeded["session_id"])[0]["id"]

    r = client.post(f"/api/rallies/{rally_id}/note", json={"note": "x" * 120})
    assert r.status_code == 200


def test_note_route_trims_before_measuring_length(client, conn, seeded):
    # The validator must trim whitespace before measuring length. This test
    # proves it: 120 characters of text is legal, but only if we measure AFTER
    # trimming. A buggy implementation that measures before trimming rejects
    # "  " + "x" * 120 + "  " as 124 characters raw, even though the trimmed
    # string is exactly at the cap and what the reviewer will actually see. No
    # other note test can tell the two implementations apart — they all omit
    # whitespace at the boundary.
    rally_id = list_rallies(conn, seeded["session_id"])[0]["id"]

    padded_note = "  " + "x" * 120 + "  "
    assert len(padded_note) == 124  # Raw string is over the cap

    r = client.post(f"/api/rallies/{rally_id}/note", json={"note": padded_note})
    assert r.status_code == 200

    # The stored note is trimmed to exactly 120 characters
    assert list_rallies(conn, seeded["session_id"])[0]["note"] == "x" * 120


def test_note_route_clears_a_note_with_an_empty_string(client, conn, seeded):
    # Deleting a note is the same write as setting one. A separate DELETE
    # route would be a second code path for "the note is now empty".
    rally_id = list_rallies(conn, seeded["session_id"])[0]["id"]

    client.post(f"/api/rallies/{rally_id}/note", json={"note": "typo"})
    client.post(f"/api/rallies/{rally_id}/note", json={"note": ""})

    assert list_rallies(conn, seeded["session_id"])[0]["note"] == ""


def test_missing_spa_serves_an_explanation_not_a_blank_page(library, tmp_path):
    with TestClient(create_app(library, spa_dist=tmp_path / "nowhere")) as c:
        r = c.get("/")
    assert r.status_code == 503
    assert "npm run build" in r.text


def test_detect_route_queues_a_full_detect(client, conn, seeded):
    conn.execute("UPDATE sources SET status='ready' WHERE id=?", (seeded["source_id"],))
    conn.commit()
    r = client.post(f"/api/sources/{seeded['source_id']}/detect")
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] and body["already_running"] is False
    row = conn.execute("SELECT payload FROM jobs WHERE type='detect'").fetchone()
    # Full detect on purpose: the route exists for "I just assigned a play
    # region", and cached features are already quad-shaped.
    assert "reuse_features" not in row["payload"]


def test_detect_route_reports_an_already_running_job(client, conn, seeded):
    conn.execute("UPDATE sources SET status='ready' WHERE id=?", (seeded["source_id"],))
    conn.commit()
    client.post(f"/api/sources/{seeded['source_id']}/detect")
    r = client.post(f"/api/sources/{seeded['source_id']}/detect")
    assert r.status_code == 200
    assert r.json() == {"job_id": None, "already_running": True}


def test_detect_route_refuses_a_source_awaiting_setup(client, conn, seeded):
    conn.execute("UPDATE sources SET status='needs_setup' WHERE id=?",
                 (seeded["source_id"],))
    conn.commit()
    assert client.post(f"/api/sources/{seeded['source_id']}/detect").status_code == 409


def test_detect_route_404s_an_unknown_source(client, seeded):
    assert client.post("/api/sources/nope/detect").status_code == 404


def test_jobs_route_carries_error_detail(client, conn, seeded):
    job_id = jobq.enqueue(conn, "detect", {"source_id": seeded["source_id"]})
    jobq.finish(conn, job_id, error="it broke", error_detail="Traceback...")
    jobs = client.get("/api/jobs").json()
    failed = next(j for j in jobs if j["id"] == job_id)
    assert failed["error"] == "it broke"
    assert failed["error_detail"] == "Traceback..."


def test_retry_route_requeues_only_failed_jobs(client, conn, seeded):
    job_id = jobq.enqueue(conn, "detect", {"source_id": seeded["source_id"]})
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 409
    jobq.finish(conn, job_id, error="boom")
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 200
    assert client.post("/api/jobs/nope/retry").status_code == 404


def test_import_streams_into_the_inbox(client, library):
    r = client.post("/api/import",
                    files={"file": ("IMG_1234.MOV", b"fake video bytes")})
    assert r.status_code == 200
    name = r.json()["name"]
    assert (library.inbox / name).read_bytes() == b"fake video bytes"
    # No half-written temp left behind, and nothing dot-prefixed for the
    # watcher to trip on.
    assert [p.name for p in library.inbox.iterdir()] == [name]


def test_import_refuses_a_non_video_suffix(client, library):
    r = client.post("/api/import", files={"file": ("notes.txt", b"hi")})
    assert r.status_code == 415
    assert list(library.inbox.iterdir()) == []


def test_import_keeps_both_files_on_a_name_collision(client, library):
    client.post("/api/import", files={"file": ("a.mp4", b"one")})
    client.post("/api/import", files={"file": ("a.mp4", b"two")})
    names = sorted(p.name for p in library.inbox.iterdir())
    assert len(names) == 2 and names[0] == "a.mp4" and names[1].endswith(".mp4")


def test_import_strips_any_client_path_from_the_filename(client, library):
    r = client.post("/api/import", files={"file": ("../../evil.mp4", b"x")})
    assert r.status_code == 200
    assert r.json()["name"] == "evil.mp4"
    assert (library.inbox / "evil.mp4").exists()


def test_import_strips_leading_dots_so_the_file_is_not_hidden(client, library):
    # A dot-prefixed name would land invisible to both the watcher (which
    # skips dotfiles on purpose) and the inbox listing -- forever, since
    # nothing ever revisits an already-imported file.
    r = client.post("/api/import", files={"file": (".hidden.mp4", b"x")})
    assert r.status_code == 200
    assert r.json()["name"] == "hidden.mp4"
    assert (library.inbox / "hidden.mp4").exists()
    assert [p.name for p in library.inbox.iterdir()] == ["hidden.mp4"]


def test_import_refuses_a_bare_dot_suffix(client, library):
    # ".mp4" has no basename once its leading dot is stripped for the check
    # above -- correctly falls through to the same 415 a suffixless upload gets.
    r = client.post("/api/import", files={"file": (".mp4", b"x")})
    assert r.status_code == 415
    assert list(library.inbox.iterdir()) == []


def test_inbox_route_reports_unsupported_and_quarantined_files(client, library):
    (library.inbox / "match.webm").write_bytes(b"x")
    failed = library.inbox / "failed"
    failed.mkdir()
    (failed / "broken.mp4").write_bytes(b"x")
    (failed / "broken.mp4.error.txt").write_text("ProbeError: no video stream")
    body = client.get("/api/inbox").json()
    assert body["unsupported"] == ["match.webm"]
    assert body["failed"] == [
        {"name": "broken.mp4", "error": "ProbeError: no video stream"}
    ]


def test_inbox_route_is_empty_when_the_inbox_is(client, library):
    assert client.get("/api/inbox").json() == {"unsupported": [], "failed": []}


def _cut(library, session_id, source_idx, start_ms, end_ms, payload=b"pretend clip bytes"):
    """Stand in for a finished encode: a file at the nested name a real
    clip job would have written, mirroring test_orphans.py's `_cut`.
    """
    path = library.clips_dir(session_id) / clip_relpath(source_idx, start_ms, end_ms)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_clips_route_lists_nested_clips_sorted_by_source_then_start(client, library, conn, seeded):
    session_id, idx = seeded["session_id"], seeded["idx"]
    _second_id, second_idx = add_source(
        conn, session_id, recorded_at="2026-08-19T11:00:00Z", duration_ms=60_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_0002.MOV",
    )
    # Written out of order on purpose -- the route sorts, the filesystem
    # walk (by directory listing order) does not.
    _cut(library, session_id, second_idx, 500, 2000, payload=b"cd")
    _cut(library, session_id, idx, 9000, 14000, payload=b"ab")
    _cut(library, session_id, idx, 1000, 5000, payload=b"a")

    body = client.get(f"/api/sessions/{session_id}/clips").json()
    assert body["clips"] == [
        {"source_idx": idx, "start_ms": 1000, "end_ms": 5000,
         "relpath": clip_relpath(idx, 1000, 5000), "size_bytes": 1},
        {"source_idx": idx, "start_ms": 9000, "end_ms": 14000,
         "relpath": clip_relpath(idx, 9000, 14000), "size_bytes": 2},
        {"source_idx": second_idx, "start_ms": 500, "end_ms": 2000,
         "relpath": clip_relpath(second_idx, 500, 2000), "size_bytes": 2},
    ]


def test_clips_route_excludes_foreign_and_in_flight_files(client, library, seeded):
    session_id, idx = seeded["session_id"], seeded["idx"]
    _cut(library, session_id, idx, 9000, 14000)
    clips_dir = library.clips_dir(session_id)
    # A file we never wrote, dropped directly in clips/ by the user.
    (clips_dir / "notes.txt").write_bytes(b"not a clip")
    # make_clip's own temp-file shape: a live encode's dot-prefixed .part
    # sibling, which parse_clip_name is deliberately strict enough to reject.
    (clips_dir / ".01-9000-14000.deadbeef.part.mp4").write_bytes(b"in flight")

    body = client.get(f"/api/sessions/{session_id}/clips").json()
    assert body["clips"] == [
        {"source_idx": idx, "start_ms": 9000, "end_ms": 14000,
         "relpath": clip_relpath(idx, 9000, 14000), "size_bytes": 18},
    ]


def test_clips_route_is_empty_before_any_export(client, seeded):
    # clips/ is created by the first encode -- see find_orphan_clips'
    # docstring. Its absence is the ordinary pre-export state, not an error.
    body = client.get(f"/api/sessions/{seeded['session_id']}/clips").json()
    assert body == {"clips": []}


def test_clips_route_404s_for_an_unknown_session(client):
    assert client.get("/api/sessions/nope/clips").status_code == 404


def test_clip_media_returns_full_body(client, library, seeded):
    session_id, idx = seeded["session_id"], seeded["idx"]
    _cut(library, session_id, idx, 9000, 14000, payload=b"0123456789")
    r = client.get(f"/media/clips/{session_id}/{idx}/9000-14000.mp4")
    assert r.status_code == 200
    assert r.content == b"0123456789"


def test_clip_media_serves_partial_content_for_a_range(client, library, seeded):
    session_id, idx = seeded["session_id"], seeded["idx"]
    _cut(library, session_id, idx, 9000, 14000, payload=b"0123456789")
    r = client.get(f"/media/clips/{session_id}/{idx}/9000-14000.mp4",
                   headers={"Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.content == b"2345"


def test_clip_media_404s_for_a_non_clip_name(client, library, seeded):
    session_id, idx = seeded["session_id"], seeded["idx"]
    # A name parse_clip_name never admits must 404 before any path is even
    # built, whether or not a file happens to sit there.
    clips_dir = library.clips_dir(session_id)
    (clips_dir / f"{idx:02d}").mkdir(parents=True, exist_ok=True)
    (clips_dir / f"{idx:02d}" / "evil.txt").write_bytes(b"not a clip")
    assert client.get(f"/media/clips/{session_id}/{idx}/evil.txt").status_code == 404


def test_clip_media_404s_for_a_parseable_but_absent_clip(client, seeded):
    session_id, idx = seeded["session_id"], seeded["idx"]
    # Well-formed name, nothing on disk -- range_response's own is_file()
    # check is what 404s this, not the parse gate.
    assert client.get(f"/media/clips/{session_id}/{idx}/9999-19999.mp4").status_code == 404


def _record_calls(monkeypatch):
    """Stand a recorder in for subprocess.run so a reveal test can assert on
    the argv shape without actually popping Finder open on the test runner.
    """
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append((argv, kw)))
    return calls


def test_reveal_endpoint_reveals_a_file_on_darwin(client, library, monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    calls = _record_calls(monkeypatch)
    target = library.root / "sessions" / "2026-08-18" / "clips" / "01" / "9000-14000.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"clip")

    r = client.post("/api/clips/reveal",
                     json={"relpath": str(target.relative_to(library.root))})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == ["open", "-R", str(target)]
    assert kwargs.get("check") is False


def test_reveal_endpoint_reveals_a_directory_on_darwin(client, library, monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    calls = _record_calls(monkeypatch)
    clips_dir = library.root / "sessions" / "2026-08-18" / "clips"
    clips_dir.mkdir(parents=True)

    r = client.post("/api/clips/reveal",
                     json={"relpath": str(clips_dir.relative_to(library.root))})
    assert r.status_code == 200
    argv, kwargs = calls[0]
    assert argv == ["open", str(clips_dir)]
    assert kwargs.get("check") is False


def test_reveal_endpoint_refuses_a_relative_escape(client, library, tmp_path, monkeypatch):
    calls = _record_calls(monkeypatch)
    # Same failure mode test_reel_media_404s_for_an_escaping_relative_rendered_path
    # covers for reels: a ".." relative path resolves outside the library
    # root just as surely as an absolute one, and must not reach the
    # subprocess call. Disambiguated by library.root.name rather than a
    # fixed filename, since tmp_path's parent is the shared pytest basetemp.
    outside = tmp_path.parent / f"outside-{library.root.name}.txt"
    outside.write_bytes(b"x")
    r = client.post("/api/clips/reveal", json={"relpath": f"../{outside.name}"})
    assert r.status_code == 404
    assert calls == []


def test_reveal_endpoint_refuses_an_absolute_path(client, library, monkeypatch, tmp_path_factory):
    calls = _record_calls(monkeypatch)
    outside_dir = tmp_path_factory.mktemp("outside")
    outside = outside_dir / "secret.txt"
    outside.write_bytes(b"x")
    # Path.__truediv__ silently discards library.root here -- library.root /
    # str(outside) is just outside again, since outside is absolute (the
    # same trap rendered_file's docstring documents for reels). The
    # resolve-then-compare containment check has to catch it anyway.
    r = client.post("/api/clips/reveal", json={"relpath": str(outside)})
    assert r.status_code == 404
    assert calls == []


def test_reveal_endpoint_404s_for_a_missing_file(client, library, monkeypatch):
    calls = _record_calls(monkeypatch)
    r = client.post("/api/clips/reveal",
                     json={"relpath": "sessions/2026-08-18/clips/01/9000-14000.mp4"})
    assert r.status_code == 404
    assert calls == []


def test_reveal_endpoint_does_not_shell_out_on_non_darwin(client, library, monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    calls = _record_calls(monkeypatch)
    target = library.root / "sessions" / "2026-08-18" / "clips" / "01" / "9000-14000.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"clip")

    r = client.post("/api/clips/reveal",
                     json={"relpath": str(target.relative_to(library.root))})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "reason": "reveal is only supported on macOS"}
    assert calls == []
