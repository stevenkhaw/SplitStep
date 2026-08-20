import os
import subprocess
from pathlib import Path

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
from bootleg.detect.features import FeatureFrame, Player, write_features
from bootleg.detect.segment import Interval
from bootleg.media.transcode import TranscodeError


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


def _write_features(library, session_id, idx, n=40):
    frames = [
        FeatureFrame(i * 200, 2,
                     Player(0.5, 0.9, 0.30, 2.5), Player(0.5, 0.4, 0.10, 2.5),
                     hits=1, hit_reg=0.9)
        for i in range(n)
    ]
    d = library.source_dir(session_id, idx)
    d.mkdir(parents=True, exist_ok=True)
    write_features(d / "features.jsonl", frames)


def test_scores_returns_one_value_per_sampled_frame(client, library, seeded):
    _write_features(library, seeded["session_id"], seeded["idx"], n=40)
    r = client.get(f"/api/sources/{seeded['source_id']}/scores")
    assert r.status_code == 200
    body = r.json()
    assert len(body["scores"]) == 40
    assert body["step_ms"] == 200


def test_scores_are_normalized_zero_to_one(client, library, seeded):
    _write_features(library, seeded["session_id"], seeded["idx"], n=40)
    scores = client.get(f"/api/sources/{seeded['source_id']}/scores").json()["scores"]
    assert all(0.0 <= s <= 1.0 for s in scores)


def test_scores_echo_the_requested_threshold(client, library, seeded):
    _write_features(library, seeded["session_id"], seeded["idx"], n=40)
    body = client.get(
        f"/api/sources/{seeded['source_id']}/scores?threshold=0.31"
    ).json()
    assert body["threshold"] == pytest.approx(0.31)


def test_scores_before_detection_is_409(client, seeded):
    r = client.get(f"/api/sources/{seeded['source_id']}/scores")
    assert r.status_code == 409


def test_scores_unknown_source_is_404(client):
    assert client.get("/api/sources/nope/scores").status_code == 404


def test_scores_for_a_single_frame_file_uses_the_default_step(client, library, seeded):
    d = library.source_dir(seeded["session_id"], seeded["idx"])
    d.mkdir(parents=True, exist_ok=True)
    write_features(d / "features.jsonl", [
        FeatureFrame(0, 2, Player(0.5, 0.9, 0.30, 2.5), Player(0.5, 0.4, 0.10, 2.5),
                     hits=1, hit_reg=0.9),
    ])
    body = client.get(f"/api/sources/{seeded['source_id']}/scores").json()
    assert body["step_ms"] == 200
    assert len(body["scores"]) == 1


def test_scores_clamps_step_ms_to_one_for_duplicate_leading_timestamps(client, library, seeded):
    """A malformed features.jsonl whose first two timestamps are equal (or
    decreasing) must not produce step_ms: 0 -- the timeline places each
    score point on the x-axis by dividing time by step_ms, so a zero step
    would be a client-side division by zero, not just a cosmetic glitch.
    """
    d = library.source_dir(seeded["session_id"], seeded["idx"])
    d.mkdir(parents=True, exist_ok=True)
    write_features(d / "features.jsonl", [
        FeatureFrame(1000, 2, Player(0.5, 0.9, 0.30, 2.5), Player(0.5, 0.4, 0.10, 2.5),
                     hits=1, hit_reg=0.9),
        FeatureFrame(1000, 2, Player(0.5, 0.9, 0.30, 2.5), Player(0.5, 0.4, 0.10, 2.5),
                     hits=1, hit_reg=0.9),
    ])
    body = client.get(f"/api/sources/{seeded['source_id']}/scores").json()
    assert body["step_ms"] == 1


def test_scores_with_non_finite_threshold_is_422(client, library, seeded):
    """Pydantic accepts nan/inf/1e400 for a bare float query param; without
    an explicit range, a cleared numeric input in the UI serializing to
    "NaN" would sail through as a valid float and only blow up later in
    Starlette's JSON renderer. ge=0.0/le=1.0 turns it into a clean 422
    here instead, since nan >= 0.0 is False.
    """
    _write_features(library, seeded["session_id"], seeded["idx"], n=40)
    r = client.get(f"/api/sources/{seeded['source_id']}/scores?threshold=NaN")
    assert r.status_code == 422


def test_resegment_with_non_finite_threshold_is_422(client, seeded):
    # A raw float("inf")/float("nan") can't even be sent here: httpx's own
    # JSON encoder rejects non-finite floats client-side (allow_nan=False)
    # before a request is made at all. The string form is what a client
    # sending a stringified numeric field would actually put on the wire,
    # and pydantic parses "NaN" into a non-finite float same as it would
    # from a query string -- so this still exercises the ge/le constraint.
    r = client.post(
        f"/api/sources/{seeded['source_id']}/resegment", json={"threshold": "NaN"}
    )
    assert r.status_code == 422


def test_resegment_drops_a_reviewed_session_back_to_ready(client, conn, library, seeded):
    """replace_rallies inserts every new rally with reviewed_at NULL and
    carries starred/rejected across by overlap, but never reviewed_at.
    api_resegment must refresh the session's review status itself -- the
    session list is how the user picks what to review next, so a session
    that still reads 'reviewed' after a re-segment that produced unseen
    rallies would be a wrong answer they act on.
    """
    _write_features(library, seeded["session_id"], seeded["idx"], n=40)
    for rally in list_rallies(conn, seeded["session_id"]):
        mark_reviewed(conn, rally["id"])
    refresh_session_review_status(conn, seeded["session_id"])
    assert get_session(conn, seeded["session_id"])["status"] == "reviewed"

    r = client.post(f"/api/sources/{seeded['source_id']}/resegment", json={})
    assert r.status_code == 200
    assert r.json()["count"] > 0
    assert r.json()["session_status"] == "ready"
    assert get_session(conn, seeded["session_id"])["status"] == "ready"


def test_resegment_producing_zero_intervals_also_leaves_the_session_ready(
    client, conn, library, seeded
):
    """Worst case of the same bug: a threshold raised too far yields zero
    rallies, and the session must not be left sitting at 'reviewed' with no
    rallies at all to review.
    """
    _write_features(library, seeded["session_id"], seeded["idx"], n=40)
    for rally in list_rallies(conn, seeded["session_id"]):
        mark_reviewed(conn, rally["id"])
    refresh_session_review_status(conn, seeded["session_id"])
    assert get_session(conn, seeded["session_id"])["status"] == "reviewed"

    r = client.post(f"/api/sources/{seeded['source_id']}/resegment", json={"threshold": 1.0})
    assert r.status_code == 200
    assert r.json()["count"] == 0
    assert r.json()["session_status"] == "ready"
    assert get_session(conn, seeded["session_id"])["status"] == "ready"


def test_create_preset_returns_an_id(client):
    r = client.post("/api/court_presets", json={
        "name": "Memorial court 3",
        "points": [[0.30, 0.32], [0.70, 0.32], [0.98, 1.0], [0.02, 1.0]],
    })
    assert r.status_code == 200
    assert r.json()["id"]


def test_created_preset_appears_in_the_list(client):
    client.post("/api/court_presets", json={
        "name": "Memorial court 3",
        "points": [[0.30, 0.32], [0.70, 0.32], [0.98, 1.0], [0.02, 1.0]],
    })
    body = client.get("/api/court_presets").json()
    assert len(body) == 1
    assert body[0]["name"] == "Memorial court 3"
    assert body[0]["points"][0] == [0.30, 0.32]


def test_preset_with_three_points_is_422(client):
    r = client.post("/api/court_presets", json={
        "name": "bad", "points": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
    })
    assert r.status_code == 422


def test_preset_with_out_of_range_point_is_422(client):
    r = client.post("/api/court_presets", json={
        "name": "bad",
        "points": [[0.0, 0.0], [1.4, 0.0], [1.0, 1.0], [0.0, 1.0]],
    })
    assert r.status_code == 422


def test_frame_endpoint_404s_when_the_proxy_is_missing(client, seeded):
    """A router miss also 404s, so the status code alone still passes even
    if this whole route were deleted -- verified by deleting api_frame and
    re-running this test, which then failed with a 404 whose body did not
    say "Proxy not found" (FastAPI's default unmatched-route body is
    {"detail": "Not Found"}). Asserting on the endpoint's own detail string
    is what actually proves this route ran its proxy-missing check.
    """
    r = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/frame.jpg")
    assert r.status_code == 404
    assert r.json()["detail"] == "Proxy not found"


def test_unrouted_media_path_404s_with_a_different_detail(client, seeded):
    """Distinguishes a genuine router miss from the frame endpoint's own
    404, so the two 404 paths are visibly different in what they report --
    the sibling to the test above.
    """
    r = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/nonexistent.jpg")
    assert r.status_code == 404
    assert r.json()["detail"] != "Proxy not found"


def _write_clip(path, color, size, duration=1):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"color=c={color}:size={size}:rate=10:duration={duration}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )


def test_stale_cached_frame_is_regenerated_when_the_proxy_changes(client, library, seeded):
    """jobs/handlers.py's crash-retry ingest path reuses the same
    source_dir and overwrites proxy.mp4 in place (see the comment on
    get_source_by_original_name reuse there). Serving a frame cached before
    that reuse would put the quad editor's still out of sync with the
    footage a review session is actually scoring -- the endpoint must
    regenerate whenever the cached frame predates the proxy on disk, not
    just when the frame file is missing.
    """
    d = library.source_dir(seeded["session_id"], seeded["idx"])
    d.mkdir(parents=True, exist_ok=True)
    proxy = d / "proxy.mp4"
    _write_clip(proxy, "red", "320x240")

    r1 = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/frame.jpg")
    assert r1.status_code == 200
    frame = d / "frame-0.jpg"
    old_bytes = frame.read_bytes()

    # Simulate the crash-retry: proxy.mp4 rewritten in place with different
    # footage, forced to a later mtime than the frame cached above --
    # os.utime rather than a real sleep, so the assertion doesn't depend on
    # filesystem timestamp resolution or wall-clock timing.
    _write_clip(proxy, "blue", "640x480")
    newer = proxy.stat().st_mtime + 5
    os.utime(proxy, (newer, newer))

    r2 = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/frame.jpg")
    assert r2.status_code == 200
    assert frame.read_bytes() != old_bytes


def test_frame_endpoint_clamps_at_ms_past_the_clip_duration(client, conn, library, seeded):
    """Scrubbing past a clip's end is ordinary UI behaviour in the quad
    editor. Past end-of-stream ffmpeg fails with exit 234 and a misleading
    "Non full-range YUV is non-standard" message -- the same end-of-stream
    encoder bug make_thumbs already documents and clamps against
    (bootleg/media/transcode.py). at_ms must be clamped into the clip's
    duration, not passed straight through to ffmpeg.

    The clamp margin now comes from the sources row (duration_ms/fps)
    rather than probing proxy.mp4 -- see api_frame. Sync the row to this
    test's actual 2s@10fps clip (the seeded fixture's own duration_ms/fps
    are arbitrary placeholders, unrelated to any real proxy file) so the
    clamp math is exercised against the same duration the fake clip has.
    """
    d = library.source_dir(seeded["session_id"], seeded["idx"])
    d.mkdir(parents=True, exist_ok=True)
    _write_clip(d / "proxy.mp4", "red", "320x240", duration=2)
    conn.execute(
        "UPDATE sources SET duration_ms = ?, fps = ? WHERE id = ?",
        (2000, 10.0, seeded["source_id"]),
    )
    conn.commit()

    r = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/frame.jpg?at_ms=5000")
    assert r.status_code == 200
    assert len(r.content) > 0


def test_frame_endpoint_clamps_negative_at_ms_to_the_cached_zero_frame(client, library, seeded):
    """A negative at_ms must be clamped to 0 explicitly rather than left for
    ffmpeg to silently clamp -- otherwise every distinct negative value
    ffmpeg happens to treat as "start of clip" leaves its own duplicate
    frame-{at_ms}.jpg on disk (frame--500.jpg, frame--999.jpg, ...) even
    though the bytes are identical to frame-0.jpg.
    """
    d = library.source_dir(seeded["session_id"], seeded["idx"])
    d.mkdir(parents=True, exist_ok=True)
    _write_clip(d / "proxy.mp4", "red", "320x240", duration=2)

    r0 = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/frame.jpg?at_ms=0")
    rneg = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/frame.jpg?at_ms=-500")
    assert r0.status_code == 200
    assert rneg.status_code == 200
    assert rneg.content == r0.content
    assert not (d / "frame--500.jpg").exists()
    assert (d / "frame-0.jpg").exists()


def test_frame_endpoint_regenerates_on_a_same_tick_mtime_tie(client, library, seeded):
    """`<` between the cached frame's mtime and the proxy's mtime never
    fires when both land in the same mtime tick -- plausible on
    coarse-granularity filesystems or fast successive writes. Pin the tie
    directly (rather than relying on real timing) and confirm the frame
    still regenerates on a tie, not just on a strictly-newer proxy.
    """
    d = library.source_dir(seeded["session_id"], seeded["idx"])
    d.mkdir(parents=True, exist_ok=True)
    proxy = d / "proxy.mp4"
    _write_clip(proxy, "red", "320x240")

    r1 = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/frame.jpg")
    assert r1.status_code == 200
    frame = d / "frame-0.jpg"
    old_bytes = frame.read_bytes()

    _write_clip(proxy, "blue", "640x480")
    tied = proxy.stat().st_mtime
    os.utime(frame, (tied, tied))
    os.utime(proxy, (tied, tied))
    assert frame.stat().st_mtime == proxy.stat().st_mtime

    r2 = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/frame.jpg")
    assert r2.status_code == 200
    assert frame.read_bytes() != old_bytes


def test_frame_cache_keeps_at_most_20_files(client, library, seeded):
    """Every distinct at_ms leaves a permanent frame-{at_ms}.jpg with no
    eviction otherwise -- an editor session scrubbing through many
    timestamps would grow this directory without bound.
    """
    d = library.source_dir(seeded["session_id"], seeded["idx"])
    d.mkdir(parents=True, exist_ok=True)
    _write_clip(d / "proxy.mp4", "red", "320x240", duration=30)

    for at_ms in range(0, 25000, 1000):
        r = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/frame.jpg?at_ms={at_ms}")
        assert r.status_code == 200

    assert len(list(d.glob("frame-*.jpg"))) <= 20


def test_repeat_frame_request_does_not_re_extract_the_cached_file(
    client, library, seeded, monkeypatch
):
    """Every other frame-endpoint test here covers the staleness branch. A
    regression that disabled caching entirely -- re-running ffmpeg on every
    request -- would pass all of them; this is the one test that would
    catch it: a repeat request for the same at_ms must not call
    extract_frame again.

    A cache hit now calls os.utime on the cached file (finding 3b), so
    mtime alone can no longer prove non-re-extraction the way it used to --
    assert directly on the call count instead.
    """
    d = library.source_dir(seeded["session_id"], seeded["idx"])
    d.mkdir(parents=True, exist_ok=True)
    _write_clip(d / "proxy.mp4", "red", "320x240")

    import bootleg.api.routes as routes_mod
    real_extract_frame = routes_mod.extract_frame
    calls = []

    def _counting(src, dst, at_ms=0):
        calls.append(at_ms)
        real_extract_frame(src, dst, at_ms=at_ms)

    monkeypatch.setattr("bootleg.api.routes.extract_frame", _counting)

    r1 = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/frame.jpg")
    assert r1.status_code == 200
    r2 = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/frame.jpg")
    assert r2.status_code == 200
    assert calls == [0]


def test_frame_cache_hit_bumps_the_cached_files_mtime(client, library, seeded):
    """A cache hit must call os.utime on the cached file: this both keeps a
    hot frame from being evicted mid-response by a concurrent eviction
    sweep (finding 3b) and turns eviction from write-time-based into
    access-time-based (finding 8) -- a frame still being scrubbed through
    stays hot instead of aging out.
    """
    d = library.source_dir(seeded["session_id"], seeded["idx"])
    d.mkdir(parents=True, exist_ok=True)
    _write_clip(d / "proxy.mp4", "red", "320x240")

    r1 = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/frame.jpg")
    assert r1.status_code == 200
    frame = d / "frame-0.jpg"
    stale = frame.stat().st_mtime - 100
    os.utime(frame, (stale, stale))

    r2 = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/frame.jpg")
    assert r2.status_code == 200
    assert frame.stat().st_mtime > stale


def test_evict_old_frames_tolerates_a_file_vanishing_mid_sweep(tmp_path, monkeypatch):
    """A concurrent eviction pass (another request racing this one) can
    unlink a file between _evict_old_frames's own glob and its stat (sort
    key). That race must not bubble an OSError out of a request that
    otherwise succeeded -- eviction is best-effort housekeeping.
    """
    from bootleg.api.routes import _evict_old_frames

    for i in range(25):
        (tmp_path / f"frame-{i}.jpg").write_bytes(b"x")

    real_stat = Path.stat

    def _flaky_stat(self, *args, **kwargs):
        if self.name == "frame-5.jpg":
            self.unlink(missing_ok=True)
            raise FileNotFoundError(self)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _flaky_stat)

    _evict_old_frames(tmp_path, keep=20)  # must not raise

    # os.path.exists (not Path.exists) so this check itself doesn't route
    # back through the still-active _flaky_stat patch.
    assert not os.path.exists(tmp_path / "frame-5.jpg")


def test_frame_endpoint_409s_when_extraction_fails(client, library, seeded, monkeypatch):
    """Any residual ffmpeg failure -- not just the out-of-range at_ms case,
    which is now clamped away before reaching ffmpeg -- must be a clean
    client error, not a bare 500. TranscodeError (and ProbeError, see the
    test below) both mean the proxy is not a complete, valid video yet, a
    normal transient state rather than a server error, so this is a 409
    rather than the 422 it used to be.
    """
    d = library.source_dir(seeded["session_id"], seeded["idx"])
    d.mkdir(parents=True, exist_ok=True)
    _write_clip(d / "proxy.mp4", "red", "320x240")

    def _boom(*args, **kwargs):
        raise TranscodeError("ffmpeg failed (exit 234)\nNon full-range YUV is non-standard")

    monkeypatch.setattr("bootleg.api.routes.extract_frame", _boom)

    r = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/frame.jpg")
    assert r.status_code == 409
    assert r.json()["detail"] == "Source is still being processed"


def test_frame_endpoint_409s_when_the_proxy_is_a_truncated_stub(client, library, seeded):
    """Regression test for finding 1/4: a source mid-ingest (or a
    crash-retry that has not finished rewriting proxy.mp4 yet) can leave a
    proxy.mp4 that exists but is not a complete, valid video. Before this
    fix, api_frame's own probe(proxy) call raised ProbeError here on every
    single request -- uncaught, so a bare 500. That call is gone now
    (duration_ms/fps come from the sources row instead), and extract_frame
    failing against the stub is caught alongside TranscodeError -- so this
    must be a clean 409, not a 500.
    """
    d = library.source_dir(seeded["session_id"], seeded["idx"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "proxy.mp4").write_bytes(b"not a real mp4")

    r = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/frame.jpg")
    assert r.status_code == 409
    assert r.json()["detail"] == "Source is still being processed"


def test_index_is_served_when_the_spa_is_built(library, tmp_path):
    dist = library.root / "webdist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>BootlegVision</title>")
    (dist / "assets" / "app.js").write_text("console.log('hi')")

    from bootleg.api.app import create_app
    from bootleg.api.spa import mount_spa

    app = create_app(library)
    mount_spa(app, dist)
    with TestClient(app) as c:
        assert "BootlegVision" in c.get("/").text
        assert c.get("/assets/app.js").status_code == 200


def test_api_routes_still_work_with_the_spa_mounted(library):
    dist = library.root / "webdist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>")

    from bootleg.api.app import create_app
    from bootleg.api.spa import mount_spa

    app = create_app(library)
    mount_spa(app, dist)
    with TestClient(app) as c:
        assert c.get("/api/sessions").status_code == 200


def test_missing_dist_is_tolerated(library):
    """Running `bootleg serve` before the UI is built must not crash."""
    from bootleg.api.app import create_app
    from bootleg.api.spa import mount_spa

    app = create_app(library)
    mount_spa(app, library.root / "does-not-exist")
    with TestClient(app) as c:
        assert c.get("/api/sessions").status_code == 200


def test_create_app_wires_the_spa_mount_after_the_api_routes(library, seeded):
    """A `StaticFiles` mount at `/` swallows every unmatched path if it is
    registered before the API router. The three tests above don't catch a
    regression in that ordering: `test_api_routes_still_work_with_the_spa_mounted`
    calls `create_app(library)` with no `spa_dist`, then mounts `mount_spa`
    manually *after* `create_app` has already returned with the router
    included -- so it never exercises `create_app`'s own internal ordering,
    only that `mount_spa` doesn't shadow routes already present on the app.

    This test goes through the real production entry point instead --
    `create_app(library, spa_dist=dist)`, exactly how `cmd_serve` calls it --
    so a regression in that internal ordering actually fails it. It also
    checks a real `/media` route (not just `/api`), using the `seeded`
    fixture for a real session/idx: a 404 with the route's own
    `"Not found: proxy.mp4"` detail proves `api_proxy` ran and hit its own
    file-existence check, whereas a bare generic `"Not Found"` would mean
    the static mount swallowed the request before it ever reached the
    router.
    """
    dist = library.root / "webdist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>BootlegVision</title>")
    (dist / "assets" / "app.js").write_text("console.log('hi')")

    app = create_app(library, spa_dist=dist)
    with TestClient(app) as c:
        assert "BootlegVision" in c.get("/").text
        assert c.get("/assets/app.js").status_code == 200
        assert c.get("/api/sessions").status_code == 200

        r = c.get(f"/media/{seeded['session_id']}/{seeded['idx']}/proxy.mp4")
        assert r.status_code == 404
        assert r.json()["detail"] == "Not found: proxy.mp4"
