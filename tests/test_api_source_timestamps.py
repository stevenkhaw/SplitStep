"""The two timestamps that tell the UI a re-segment would be lying.

`features.jsonl` is written when detection runs; `preset_assigned_at` is
stamped when a play region is attached to a source. When the second is newer
than the first, the cached features were built under the *old* region, so the
re-segment slider -- which only replays those features -- silently ignores the
new one. The UI cannot say that without both timestamps, so the session
payload carries them.
"""

import json

import pytest
from fastapi.testclient import TestClient

from splitstep.api.app import create_app
from splitstep.db.presets import create_preset
from splitstep.db.schema import connect
from splitstep.db.sessions import add_source, find_or_create_session_for_date, set_source_preset
from splitstep.detect.geometry import Quad


@pytest.fixture
def client(library, conn):
    with TestClient(create_app(library)) as c:
        yield c


@pytest.fixture
def seeded(library, conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-19")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-19T10:00:00Z", duration_ms=60_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_0001.MOV",
    )
    return {"session_id": session_id, "source_id": source_id, "idx": idx}


def _source(client, session_id):
    body = client.get(f"/api/sessions/{session_id}").json()
    return body["sources"][0]


def test_features_at_is_null_before_detection_has_written_features(client, seeded):
    assert _source(client, seeded["session_id"])["features_at"] is None


def test_features_at_is_the_features_file_mtime(client, library, seeded):
    d = library.source_dir(seeded["session_id"], seeded["idx"])
    d.mkdir(parents=True, exist_ok=True)
    path = d / "features.jsonl"
    path.write_text(json.dumps({"t_ms": 0, "boxes": []}) + "\n")

    features_at = _source(client, seeded["session_id"])["features_at"]
    assert features_at is not None
    # An ISO string, so the client can compare it to preset_assigned_at with
    # a plain string comparison -- both are UTC-normalised isoformat.
    assert features_at.startswith("20")
    assert "T" in features_at


def test_preset_assigned_at_is_null_until_a_region_is_assigned(client, seeded):
    assert _source(client, seeded["session_id"])["preset_assigned_at"] is None


def test_preset_assigned_at_is_stamped_when_a_region_is_assigned(client, conn, seeded):
    preset_id = create_preset(conn, "court", Quad([(0, 0), (1, 0), (1, 1), (0, 1)]))
    set_source_preset(conn, seeded["source_id"], preset_id)

    assigned_at = _source(client, seeded["session_id"])["preset_assigned_at"]
    assert assigned_at is not None
    assert assigned_at.startswith("20")


def test_a_region_assigned_after_a_detect_sorts_newer_than_the_features(
    client, library, conn, seeded
):
    # The whole point of carrying both: the client's comparison is a plain
    # string `>` on the two ISO timestamps, so their formats have to agree
    # well enough for ordering to survive it.
    d = library.source_dir(seeded["session_id"], seeded["idx"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "features.jsonl").write_text("{}\n")

    preset_id = create_preset(conn, "court", Quad([(0, 0), (1, 0), (1, 1), (0, 1)]))
    set_source_preset(conn, seeded["source_id"], preset_id)

    src = _source(client, seeded["session_id"])
    assert src["preset_assigned_at"] > src["features_at"]


def test_the_migration_that_adds_the_column_is_applied_to_a_fresh_library(library):
    conn = connect(library.db_path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sources)")}
    assert "preset_assigned_at" in cols
