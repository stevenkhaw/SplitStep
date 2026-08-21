import os
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bootleg.api.app import create_app
from bootleg.api.routes import _evict_old_frames
from bootleg.db.presets import create_preset
from bootleg.db.rallies import list_rallies, replace_rallies, set_point, set_rejected, set_star
from bootleg.db.schema import connect
from bootleg.db.sessions import add_source, find_or_create_session_for_date
from bootleg.detect.geometry import Quad
from bootleg.detect.segment import Interval
from bootleg.media.transcode import TranscodeError


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
    from bootleg.detect.features import write_features

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

    monkeypatch.setattr("bootleg.api.routes.extract_frame", _dies_after_partial_write)

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
    monkeypatch.setattr("bootleg.api.routes.extract_frame", dies_after_writing)
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
