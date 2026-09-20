"""The threshold a source's current rallies were actually cut at.

Reported on the user's real library: source 2026-09-16/01 was re-segmented at
0.15 and now holds rallies with confidence down to 0.176 -- below the 0.25
subject-profile default. Reopen the app and the slider reads 0.25, because
nothing stored the number. The panel seeded itself from `/scores`, which
answers with the *profile default* (`params_for_frames`' resolved value), not
with whatever produced the rallies on screen.

That is a false claim about the data, not a cosmetic reset: the reviewer is
looking at 0.15 rallies under a label saying 0.25. So every writer of a
segmentation records the threshold it used, and the column is nullable
because a source segmented before this migration has no honest value --
backfilling the profile default would assert a number nobody ran.
"""

import pytest
from fastapi.testclient import TestClient

from splitstep.api.app import create_app
from splitstep.cli import main
from splitstep.db.schema import connect
from splitstep.db.sessions import add_source, find_or_create_session_for_date
from splitstep.detect.features import FeatureFrame, Player, write_features
from splitstep.detect.segment import params_for_frames
from splitstep.jobs.handlers import handle_detect


def _pair_frames() -> list[FeatureFrame]:
    """The same 8 s two-player stream test_handlers/test_cli segment with."""
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
    """A source with cached features -- no YOLO, no ffmpeg."""
    session_id = find_or_create_session_for_date(conn, "2026-08-19")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-19T10:00:00Z", duration_ms=8000,
        width=1920, height=1080, fps=30.0, original_name="IMG_0001.MOV",
    )
    src_dir = library.source_dir(session_id, idx)
    src_dir.mkdir(parents=True, exist_ok=True)
    write_features(src_dir / "features.jsonl", _pair_frames())
    return {"session_id": session_id, "source_id": source_id, "idx": idx}


def _threshold(conn, source_id):
    return conn.execute(
        "SELECT segment_threshold FROM sources WHERE id = ?", (source_id,)
    ).fetchone()["segment_threshold"]


# -- migration 015 -----------------------------------------------------------

def test_the_migration_adds_the_column_to_a_fresh_library(library):
    conn = connect(library.db_path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sources)")}
    assert "segment_threshold" in cols


def test_the_column_is_null_on_a_source_that_has_never_been_segmented(conn, seeded):
    # The whole reason the column is nullable. A row added before this
    # migration -- and a row added after it but not yet detected -- is
    # genuinely unknown, and the UI must render that as unknown rather than
    # as a number the reviewer would read as a claim.
    assert _threshold(conn, seeded["source_id"]) is None


# -- writers -----------------------------------------------------------------

def test_a_resegment_records_the_explicit_threshold_it_was_given(client, conn, seeded):
    r = client.post(f"/api/sources/{seeded['source_id']}/resegment",
                    json={"threshold": 0.15})
    assert r.status_code == 200
    assert _threshold(conn, seeded["source_id"]) == pytest.approx(0.15)


def test_a_default_resegment_records_the_resolved_profile_default(
    library, client, conn, seeded
):
    """Not a hardcoded constant: `params_for_frames` picks the profile per
    source and the two profiles' thresholds are on different scales (0.25
    subject, 0.45 pair). What gets recorded has to be the value that was
    actually scored against.
    """
    resolved = params_for_frames(_pair_frames()).threshold

    r = client.post(f"/api/sources/{seeded['source_id']}/resegment", json={})
    assert r.status_code == 200
    assert _threshold(conn, seeded["source_id"]) == pytest.approx(resolved)


def test_a_default_resegment_records_the_subject_default_on_ground_footage(
    library, client, conn, seeded, ground_features
):
    """The same claim against real footage rather than a synthetic stream.

    Ground-level source 01 classifies as subject mode, whose default is 0.25
    -- so a run that recorded the pair-mode 0.45 here would be recording a
    number nothing used. This is the case the bug report is actually about.
    """
    src_dir = library.source_dir(seeded["session_id"], seeded["idx"])
    write_features(src_dir / "features.jsonl", ground_features)

    r = client.post(f"/api/sources/{seeded['source_id']}/resegment", json={})
    assert r.status_code == 200
    assert _threshold(conn, seeded["source_id"]) == pytest.approx(0.25)


def test_detect_records_the_threshold_it_segmented_with(library, conn, seeded):
    """The detect handler is the other writer of a segmentation, and the one
    that produces the value a source starts life with.
    """
    handle_detect(library, {"source_id": seeded["source_id"], "reuse_features": True})

    resolved = params_for_frames(_pair_frames()).threshold
    assert _threshold(conn, seeded["source_id"]) == pytest.approx(resolved)


def test_the_cli_segment_command_records_it_too(library, conn, seeded, capsys):
    """HTTP and terminal must not drift on this, the same way they do not
    drift on validation (setup.py::queue_setup) or on the session review
    status refresh. A CLI sweep that left the column stale would make the
    slider lie again, just from the other direction.
    """
    rc = main(["--library", str(library.root), "segment", seeded["source_id"],
               "--threshold", "0.2"])
    assert rc == 0
    assert _threshold(conn, seeded["source_id"]) == pytest.approx(0.2)


def test_a_dry_run_records_nothing(library, conn, seeded, capsys):
    # --dry-run persists no rallies, so recording a threshold for rallies it
    # did not write would describe the ones still on disk with a number that
    # never touched them.
    rc = main(["--library", str(library.root), "segment", seeded["source_id"],
               "--threshold", "0.2", "--dry-run"])
    assert rc == 0
    assert _threshold(conn, seeded["source_id"]) is None


# -- the API serves it -------------------------------------------------------

def test_the_session_payload_carries_the_recorded_threshold(client, seeded):
    client.post(f"/api/sources/{seeded['source_id']}/resegment", json={"threshold": 0.15})

    body = client.get(f"/api/sessions/{seeded['session_id']}").json()
    assert body["sources"][0]["segment_threshold"] == pytest.approx(0.15)


def test_the_session_payload_carries_null_when_nothing_was_recorded(client, seeded):
    body = client.get(f"/api/sessions/{seeded['session_id']}").json()
    assert body["sources"][0]["segment_threshold"] is None


def test_the_single_source_route_carries_it_as_well(client, seeded):
    client.post(f"/api/sources/{seeded['source_id']}/resegment", json={"threshold": 0.15})

    body = client.get(f"/api/sources/{seeded['source_id']}").json()
    assert body["segment_threshold"] == pytest.approx(0.15)
