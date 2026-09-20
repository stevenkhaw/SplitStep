"""A detect run you can aim, and a record of the region its features were built with.

Two failures measured on the user's live library on 2026-09-20:

1. `POST /api/sources/{id}/detect` took no body, so `handle_detect` always
   segmented at `params_for_frames`' profile default. A source re-segmented
   to 0.15 and then re-detected came back at 0.25 -- fifteen minutes of GPU
   to silently discard the reviewer's threshold. There was no way to ask the
   expensive run for the number you had already chosen.

2. Nothing recorded which play region shaped the current `features.jsonl`.
   The staleness warning compared `preset_assigned_at` -- a *timestamp* --
   against the features mtime, so re-saving the identical four corners made
   the app claim the region had changed. Measured three times on one source.
   The fix is to record the preset the features were actually built under
   and let the client compare corners; a timestamp can only ever guess, and
   guessing is the bug.
"""

import json

import pytest
from fastapi.testclient import TestClient

from splitstep.api.app import create_app
from splitstep.db import jobs as jobq
from splitstep.db.presets import create_preset
from splitstep.db.schema import connect
from splitstep.db.sessions import add_source, find_or_create_session_for_date, set_source_preset
from splitstep.detect.features import FeatureFrame, Player, write_features
from splitstep.detect.geometry import Quad
from splitstep.detect.segment import params_for_frames, segment
from splitstep.jobs import handlers
from splitstep.jobs.handlers import handle_detect

QUAD_A = Quad(((0.1, 0.9), (0.9, 0.9), (0.7, 0.3), (0.3, 0.3)))
QUAD_B = Quad(((0.2, 0.8), (0.8, 0.8), (0.6, 0.4), (0.4, 0.4)))


def _pair_frames() -> list[FeatureFrame]:
    """The same 8 s two-player stream test_handlers/test_segment_threshold use."""
    return [
        FeatureFrame(i * 200, 2,
                     Player(0.5, 0.9, 0.30, 2.5), Player(0.5, 0.4, 0.10, 2.5),
                     hits=1, hit_reg=0.9)
        for i in range(40)
    ]


@pytest.fixture
def client(library, conn):
    with TestClient(create_app(library)) as c:
        yield c


@pytest.fixture
def seeded(library, conn):
    """A source with cached features on disk -- no YOLO, no ffmpeg."""
    session_id = find_or_create_session_for_date(conn, "2026-08-19")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-19T10:00:00Z", duration_ms=8000,
        width=1920, height=1080, fps=30.0, original_name="IMG_0001.MOV",
    )
    conn.execute("UPDATE sources SET status='ready' WHERE id=?", (source_id,))
    conn.commit()
    src_dir = library.source_dir(session_id, idx)
    src_dir.mkdir(parents=True, exist_ok=True)
    write_features(src_dir / "features.jsonl", _pair_frames())
    return {"session_id": session_id, "source_id": source_id, "idx": idx}


@pytest.fixture
def mocked_expensive_stage(monkeypatch):
    """Stand in for the YOLO + audio pass so the full (non-reuse) branch runs.

    Everything this feature touches lives on the cheap side of the two-stage
    split; the expensive side is mocked here exactly as CLAUDE.md requires --
    YOLO is never run in tests. `build_features` is replaced rather than fed
    empty boxes so the frames that reach `segment()` are the same stream the
    rest of this module asserts against.
    """
    seen = {}

    def fake_build_features(boxes, quad, grid, step_ms):
        seen["quad"] = quad
        return _pair_frames()

    monkeypatch.setattr(handlers, "iter_person_boxes", lambda *a, **k: iter(()))
    monkeypatch.setattr(handlers, "_audio_grid", lambda *a, **k: [])
    monkeypatch.setattr(handlers, "build_features", fake_build_features)
    return seen


def _row(conn, source_id):
    return conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()


def _threshold(conn, source_id):
    return _row(conn, source_id)["segment_threshold"]


def _features_preset(conn, source_id):
    return _row(conn, source_id)["features_preset_id"]


# -- migration 016 -----------------------------------------------------------

def test_the_migration_adds_the_column_to_a_fresh_library(library):
    conn = connect(library.db_path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sources)")}
    assert "features_preset_id" in cols


def test_the_column_is_null_on_a_source_that_has_never_been_detected(conn, seeded):
    # Why it is nullable: a row predating the migration was detected under
    # *some* region nobody recorded, and back-filling the source's current
    # court_preset_id would assert exactly the kind of guess the staleness
    # check is being fixed for.
    assert _features_preset(conn, seeded["source_id"]) is None


# -- the detect handler records the region it built features with ------------

def test_detect_records_the_preset_its_features_were_built_with(
    library, conn, seeded, mocked_expensive_stage
):
    preset_id = create_preset(conn, "court", QUAD_A)
    set_source_preset(conn, seeded["source_id"], preset_id)

    handle_detect(library, {"source_id": seeded["source_id"]})

    assert mocked_expensive_stage["quad"] == QUAD_A
    assert _features_preset(conn, seeded["source_id"]) == preset_id


def test_detect_records_no_region_when_the_source_has_none(
    library, conn, seeded, mocked_expensive_stage
):
    # Whole-frame detection is a real state, not a missing one, but it has
    # no preset row to point at -- and NULL is what the client must see so
    # it can say "no play region" rather than compare against nothing.
    handle_detect(library, {"source_id": seeded["source_id"]})
    assert _features_preset(conn, seeded["source_id"]) is None


def test_reuse_features_does_not_claim_the_current_region_built_them(
    library, conn, seeded, mocked_expensive_stage
):
    """--reuse-features replays cached features; it does not rebuild them.

    This is the divergence the column exists to expose: the features on disk
    were shaped by the region that built them, and overwriting the record
    with whatever is assigned now would erase the very difference the
    staleness warning is meant to read.
    """
    first = create_preset(conn, "first", QUAD_A)
    set_source_preset(conn, seeded["source_id"], first)
    handle_detect(library, {"source_id": seeded["source_id"]})

    second = create_preset(conn, "second", QUAD_B)
    set_source_preset(conn, seeded["source_id"], second)
    handle_detect(library, {"source_id": seeded["source_id"], "reuse_features": True})

    assert _features_preset(conn, seeded["source_id"]) == first


# -- the detect handler takes a threshold ------------------------------------

def test_detect_segments_at_an_explicit_threshold_and_records_it(library, conn, seeded):
    handle_detect(
        library, {"source_id": seeded["source_id"], "reuse_features": True, "threshold": 0.15}
    )

    expected = segment(_pair_frames(), params_for_frames(_pair_frames(), threshold=0.15))
    rallies = conn.execute(
        "SELECT start_ms, end_ms FROM rallies WHERE source_id = ? ORDER BY idx",
        (seeded["source_id"],),
    ).fetchall()
    assert [(r["start_ms"], r["end_ms"]) for r in rallies] == [
        (i.start_ms, i.end_ms) for i in expected
    ]
    assert _threshold(conn, seeded["source_id"]) == pytest.approx(0.15)


def test_detect_without_a_threshold_uses_the_resolved_profile_default(library, conn, seeded):
    """Not a constant: the two profiles sit on different scales (0.25 subject,
    0.45 pair), so `params_for_frames` is the only thing that may pick.
    """
    handle_detect(library, {"source_id": seeded["source_id"], "reuse_features": True})

    resolved = params_for_frames(_pair_frames()).threshold
    assert _threshold(conn, seeded["source_id"]) == pytest.approx(resolved)


def test_a_none_threshold_in_the_payload_is_the_profile_default_too(library, conn, seeded):
    # The API serialises an omitted threshold as an explicit null; that must
    # mean "resolve per source", never "0".
    handle_detect(
        library, {"source_id": seeded["source_id"], "reuse_features": True, "threshold": None}
    )
    resolved = params_for_frames(_pair_frames()).threshold
    assert _threshold(conn, seeded["source_id"]) == pytest.approx(resolved)


# -- the route ---------------------------------------------------------------

def test_the_route_threads_the_threshold_into_the_job_payload(client, conn, seeded):
    r = client.post(f"/api/sources/{seeded['source_id']}/detect", json={"threshold": 0.15})
    assert r.status_code == 200

    job = conn.execute("SELECT payload FROM jobs WHERE type='detect'").fetchone()
    assert '"threshold": 0.15' in job["payload"] or '"threshold":0.15' in job["payload"]


def test_the_route_still_accepts_no_body_at_all(client, conn, seeded):
    # The shipped client posts `{method: 'POST'}` with no body and no
    # content-type; a required body would 422 every existing caller.
    r = client.post(f"/api/sources/{seeded['source_id']}/detect")
    assert r.status_code == 200, r.text
    assert r.json()["job_id"] is not None


def test_the_route_rejects_a_threshold_outside_zero_to_one(client, seeded):
    for bad in (-0.1, 1.5):
        r = client.post(f"/api/sources/{seeded['source_id']}/detect", json={"threshold": bad})
        assert r.status_code == 422, bad


def test_a_threshold_does_not_let_a_second_detect_stack(client, conn, seeded):
    """enqueue_once keys on (type, payload.source_id) only.

    Mashing the button is the case it exists for, and a threshold riding in
    the payload must not become a second dedupe key -- two fifteen-minute
    detects both calling replace_rallies is how hand-edited boundaries get
    silently discarded.
    """
    first = client.post(f"/api/sources/{seeded['source_id']}/detect", json={"threshold": 0.15})
    second = client.post(f"/api/sources/{seeded['source_id']}/detect", json={"threshold": 0.4})

    assert first.json()["job_id"] is not None
    assert second.json()["job_id"] is None
    assert second.json()["already_running"] is True
    assert conn.execute("SELECT COUNT(*) c FROM jobs WHERE type='detect'").fetchone()["c"] == 1


# -- the API serves both quads -----------------------------------------------

def test_the_session_payload_carries_both_quads(
    library, client, conn, seeded, mocked_expensive_stage
):
    """Corners, not ids.

    A second preset row holding the identical four corners is not a changed
    play region, and the whole point of this change is that the client stops
    guessing. So it needs geometry on both sides of the comparison.
    """
    first = create_preset(conn, "first", QUAD_A)
    set_source_preset(conn, seeded["source_id"], first)
    handle_detect(library, {"source_id": seeded["source_id"]})

    second = create_preset(conn, "second", QUAD_B)
    set_source_preset(conn, seeded["source_id"], second)

    src = client.get(f"/api/sessions/{seeded['session_id']}").json()["sources"][0]
    assert src["features_preset_id"] == first
    assert src["court_preset_id"] == second
    assert src["features_preset_points"] == [list(p) for p in QUAD_A.points]
    assert src["court_preset_points"] == [list(p) for p in QUAD_B.points]


def test_identical_corners_under_two_preset_rows_read_as_unchanged(
    library, client, conn, seeded, mocked_expensive_stage
):
    # The measured bug, restated as the property that fixes it: the user
    # re-saved the same four corners three times and was told the region had
    # changed each time. Different ids, identical geometry.
    first = create_preset(conn, "first", QUAD_A)
    set_source_preset(conn, seeded["source_id"], first)
    handle_detect(library, {"source_id": seeded["source_id"]})

    duplicate = create_preset(conn, "same corners again", QUAD_A)
    set_source_preset(conn, seeded["source_id"], duplicate)

    src = client.get(f"/api/sessions/{seeded['session_id']}").json()["sources"][0]
    assert src["features_preset_id"] != src["court_preset_id"]
    assert src["features_preset_points"] == src["court_preset_points"]


def test_both_quads_are_null_when_no_region_is_involved(client, seeded):
    src = client.get(f"/api/sessions/{seeded['session_id']}").json()["sources"][0]
    assert src["features_preset_id"] is None
    assert src["features_preset_points"] is None
    assert src["court_preset_points"] is None


def test_the_single_source_route_carries_the_same_shape(
    library, client, conn, seeded, mocked_expensive_stage
):
    preset_id = create_preset(conn, "court", QUAD_A)
    set_source_preset(conn, seeded["source_id"], preset_id)
    handle_detect(library, {"source_id": seeded["source_id"]})

    body = client.get(f"/api/sources/{seeded['source_id']}").json()
    assert body["features_preset_id"] == preset_id
    assert body["features_preset_points"] == [list(p) for p in QUAD_A.points]
    assert body["court_preset_points"] == [list(p) for p in QUAD_A.points]


def test_the_cli_detect_command_can_pass_a_threshold(library, conn, seeded):
    """HTTP and terminal must not drift, the same way they do not on
    validation (setup.py::queue_setup) or on the recorded threshold.
    """
    from splitstep.cli import main

    rc = main(["--library", str(library.root), "detect", seeded["source_id"],
               "--reuse-features", "--threshold", "0.15", "--now"])
    assert rc == 0
    assert _threshold(conn, seeded["source_id"]) == pytest.approx(0.15)


def test_the_queued_job_is_claimable_with_a_threshold(client, conn, seeded):
    # The payload round-trips through JSON in the jobs table; a float must
    # come back a float, not a string, or params_for_frames would compare it.
    client.post(f"/api/sources/{seeded['source_id']}/detect", json={"threshold": 0.15})
    job = jobq.claim(conn)
    assert job is not None
    # claim() hands back the raw row; the Worker is what json.loads the
    # payload, so this asserts on the same string it will parse.
    assert json.loads(job["payload"])["threshold"] == pytest.approx(0.15)
