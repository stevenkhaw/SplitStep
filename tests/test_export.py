import pytest
from fastapi.testclient import TestClient

from bootleg.api.app import create_app
from bootleg.db.jobs import enqueue
from bootleg.db.rallies import replace_rallies, set_point, set_rejected, set_star
from bootleg.db.sessions import add_source, find_or_create_session_for_date
from bootleg.detect.segment import Interval
from bootleg.export import column_for, plan_export
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


def _vanish_source(conn, source_id):
    # rallies.source_id is ON DELETE CASCADE, so the ordinary path to a
    # vanished source would take its rallies with it. Toggle the pragma off
    # for this one delete to get the row shape the guard defends against --
    # a rally whose source really is gone -- without also losing the rally.
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")


def test_points_set_returns_only_points(library, conn, seeded):
    set_point(conn, seeded["rallies"][0]["id"], True)
    set_star(conn, seeded["rallies"][1]["id"], True)
    plan = plan_export(library, conn, seeded["session_id"], "points")
    assert [s["start_ms"] for s in plan.pending] == [1000]


def test_starred_set_returns_only_starred(library, conn, seeded):
    set_point(conn, seeded["rallies"][0]["id"], True)
    set_star(conn, seeded["rallies"][1]["id"], True)
    plan = plan_export(library, conn, seeded["session_id"], "starred")
    assert [s["start_ms"] for s in plan.pending] == [9000]


def test_rejected_rallies_are_never_cut(library, conn, seeded):
    # A rejection says there is no rally here, so there is nothing to cut --
    # even if the flag survived from before the rejection.
    rally = seeded["rallies"][0]
    set_point(conn, rally["id"], True)
    set_rejected(conn, rally["id"], True)
    plan = plan_export(library, conn, seeded["session_id"], "points")
    assert plan.pending == []
    assert plan.total == 0


def test_a_span_whose_clip_exists_is_skipped(library, conn, seeded):
    """The incremental property: this is what stops a second export
    re-encoding twenty-four clips."""
    rally = seeded["rallies"][0]
    set_point(conn, rally["id"], True)
    _touch_clip(library, seeded["session_id"], seeded["idx"], 1000, 5000)
    plan = plan_export(library, conn, seeded["session_id"], "points")
    assert plan.pending == []
    assert plan.already_cut == 1


def test_a_span_whose_bounds_moved_is_cut_again(library, conn, seeded):
    # No staleness record to keep in sync: the rally now resolves to a
    # different path, and that path does not exist.
    rally = seeded["rallies"][0]
    set_point(conn, rally["id"], True)
    _touch_clip(library, seeded["session_id"], seeded["idx"], 1000, 5000)
    conn.execute("UPDATE rallies SET start_ms = 1400 WHERE id = ?", (rally["id"],))
    conn.commit()
    plan = plan_export(library, conn, seeded["session_id"], "points")
    assert [s["start_ms"] for s in plan.pending] == [1400]


def test_a_span_with_a_clip_job_in_flight_is_not_queued_twice(library, conn, seeded):
    rally = seeded["rallies"][0]
    set_point(conn, rally["id"], True)
    enqueue(conn, "clip", {"source_id": seeded["source_id"], "rally_id": rally["id"],
                           "start_ms": 1000, "end_ms": 5000})
    plan = plan_export(library, conn, seeded["session_id"], "points")
    assert plan.pending == []
    assert plan.in_flight == 1


def test_an_in_flight_job_with_a_file_already_on_disk_is_in_flight_not_already_cut(
    library, conn, seeded
):
    """The real state H1 was filed against: make_clip's own docstring notes a
    killed-and-requeued job (reclaim_stale) can leave a stale, complete file
    at the exact span-derived path from an earlier attempt while a fresh job
    for the same span is queued or running right now. Before the fix,
    plan_export checked `.exists()` before `has_pending_clip`, so this state
    -- a live job PLUS a file at the path -- was reported as already_cut, the
    exact bug live-verified against the running export: 8 clips done in the
    DB but 9 .mp4s on disk, the 9th being the running job's own output.
    has_pending_clip must be consulted first, so a live job always wins.
    """
    rally = seeded["rallies"][0]
    set_point(conn, rally["id"], True)
    enqueue(conn, "clip", {"source_id": seeded["source_id"], "rally_id": rally["id"],
                           "start_ms": rally["start_ms"], "end_ms": rally["end_ms"]})
    _touch_clip(library, seeded["session_id"], seeded["idx"], rally["start_ms"], rally["end_ms"])

    plan = plan_export(library, conn, seeded["session_id"], "points")
    assert plan.pending == []
    assert plan.in_flight == 1
    assert plan.already_cut == 0


def test_a_failed_clip_jobs_span_is_re_cut_not_already_cut(library, conn, seeded):
    """The property that survives H1's atomic-rename fix, in place of the
    'truncated file with no live job' scenario the bug report described.

    Before this branch, that scenario was real: ffmpeg wrote straight to the
    final path with -y, so a killed job left a truncated file exactly there,
    the job's own row went to 'failed' (neither queued nor running, so
    has_pending_clip is False), and every later export saw the file and
    called it already_cut forever -- permanent corruption, per H1.

    make_clip's fix (see transcode.py) makes that specific scenario
    impossible by construction: it now encodes to a sibling temp path and
    os.replace()s onto the final name only after ffmpeg exits 0, so a killed
    or failed job can never leave anything at the exact span-derived path --
    only a real, complete clip lands there. What this test checks is the
    property that actually holds now: a failed job with (necessarily) no
    file on disk must come back pending, not already_cut, so the next export
    re-cuts it normally.
    """
    rally = seeded["rallies"][0]
    set_point(conn, rally["id"], True)
    job_id = enqueue(conn, "clip", {"source_id": seeded["source_id"], "rally_id": rally["id"],
                                    "start_ms": rally["start_ms"], "end_ms": rally["end_ms"]})
    conn.execute("UPDATE jobs SET status = 'failed', error = 'boom' WHERE id = ?", (job_id,))
    conn.commit()

    plan = plan_export(library, conn, seeded["session_id"], "points")
    assert [s["start_ms"] for s in plan.pending] == [rally["start_ms"]]
    assert plan.already_cut == 0


def test_plan_export_rejects_an_unknown_set(library, conn, seeded):
    with pytest.raises(ValueError, match="which must be one of"):
        plan_export(library, conn, seeded["session_id"], "everything")


def test_column_for_rejects_unknown_set_and_route_still_422s(client, seeded):
    # column_for is the one guarded place the column ternary happens now --
    # a caller that reaches it outside the API (a script, model_construct())
    # gets the same ValueError the route's pydantic validator produces as a
    # 422, rather than a silently-interpolated garbage column name.
    with pytest.raises(ValueError, match="which must be one of"):
        column_for("everything")

    r = client.post(f"/api/sessions/{seeded['session_id']}/export",
                    json={"which": "everything"})
    assert r.status_code == 422


def test_route_reports_queued_against_already_cut(client, library, conn, seeded):
    for rally in seeded["rallies"][:2]:
        set_point(conn, rally["id"], True)
    _touch_clip(library, seeded["session_id"], seeded["idx"], 1000, 5000)

    r = client.post(f"/api/sessions/{seeded['session_id']}/export",
                    json={"which": "points"})
    assert r.status_code == 200
    assert r.json() == {
        "queued": 1, "already_cut": 1, "in_flight": 0, "unavailable": 0, "total": 2,
    }
    queued = conn.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE type = 'clip'"
    ).fetchone()["n"]
    assert queued == 1


def test_route_reports_in_flight_as_in_flight_not_already_cut(client, conn, seeded):
    # Pressing export a second time while the first batch is still encoding
    # must not tell the user everything is already cut -- it isn't.
    rally = seeded["rallies"][0]
    set_point(conn, rally["id"], True)
    enqueue(conn, "clip", {"source_id": seeded["source_id"], "rally_id": rally["id"],
                           "start_ms": rally["start_ms"], "end_ms": rally["end_ms"]})

    r = client.post(f"/api/sessions/{seeded['session_id']}/export",
                    json={"which": "points"})
    assert r.status_code == 200
    body = r.json()
    assert body["in_flight"] == 1
    assert body["already_cut"] == 0
    assert body["queued"] == 0
    assert body["total"] == 1


def test_route_reports_vanished_source_as_unavailable_not_already_cut(client, conn, seeded):
    rally = seeded["rallies"][0]
    set_point(conn, rally["id"], True)
    _vanish_source(conn, seeded["source_id"])

    r = client.post(f"/api/sessions/{seeded['session_id']}/export",
                    json={"which": "points"})
    assert r.status_code == 200
    body = r.json()
    assert body["unavailable"] == 1
    assert body["already_cut"] == 0
    assert body["queued"] == 0
    assert body["total"] == 1


def test_the_four_numbers_account_for_every_rally_in_the_set(client, library, conn, seeded):
    # One rally per outcome -- pending, already cut, in flight, and a
    # vanished source -- from two different sources, so that the four
    # numbers in the response must sum to exactly the four rallies flagged
    # in the set, with none double-counted and none dropped.
    session_id = seeded["session_id"]
    pending_rally, cut_rally, flight_rally = seeded["rallies"]
    for rally in (pending_rally, cut_rally, flight_rally):
        set_point(conn, rally["id"], True)

    _touch_clip(library, session_id, seeded["idx"], cut_rally["start_ms"], cut_rally["end_ms"])
    enqueue(conn, "clip", {
        "source_id": seeded["source_id"], "rally_id": flight_rally["id"],
        "start_ms": flight_rally["start_ms"], "end_ms": flight_rally["end_ms"],
    })

    gone_source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-18T11:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9001.MOV",
    )
    replace_rallies(conn, session_id, gone_source_id, [Interval(2000, 6000, 0.8)])
    gone_rally = conn.execute(
        "SELECT * FROM rallies WHERE source_id = ?", (gone_source_id,)
    ).fetchone()
    set_point(conn, gone_rally["id"], True)
    _vanish_source(conn, gone_source_id)

    r = client.post(f"/api/sessions/{session_id}/export", json={"which": "points"})
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "queued": 1, "already_cut": 1, "in_flight": 1, "unavailable": 1, "total": 4,
    }
    assert body["queued"] + body["already_cut"] + body["in_flight"] + body["unavailable"] \
        == body["total"]


def test_route_404s_on_an_unknown_session(client, seeded):
    r = client.post("/api/sessions/nope/export", json={"which": "points"})
    assert r.status_code == 404


def test_route_422s_on_an_unknown_set(client, seeded):
    r = client.post(f"/api/sessions/{seeded['session_id']}/export",
                    json={"which": "everything"})
    assert r.status_code == 422
