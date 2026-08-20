import pytest

from bootleg.db.presets import create_preset
from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import (
    add_source,
    find_or_create_session_for_date,
    get_source,
    set_source_status,
)
from bootleg.detect.geometry import Quad
from bootleg.setup import queue_setup


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "l.db")
    migrate(c)
    return c


def _source(conn, status="needs_setup"):
    session_id = find_or_create_session_for_date(conn, "2026-08-20")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-20", duration_ms=1000,
        width=1920, height=1080, fps=30.0, original_name="a.mov",
    )
    set_source_status(conn, source_id, status)
    return source_id


def _preset(conn):
    # create_preset takes a Quad, not raw points -- see bootleg/db/presets.py.
    return create_preset(conn, "court", Quad(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))))


def test_queue_setup_stores_both_and_enqueues_build_proxy(conn):
    source_id, preset_id = _source(conn), _preset(conn)

    job_id = queue_setup(conn, source_id, 90, preset_id)

    row = get_source(conn, source_id)
    assert row["rotation_deg"] == 90
    assert row["court_preset_id"] == preset_id
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert job["type"] == "build_proxy"


def test_queue_setup_rejects_a_bad_rotation(conn):
    with pytest.raises(ValueError, match="0, 90, 180 or 270"):
        queue_setup(conn, _source(conn), 45, _preset(conn))


def test_queue_setup_rejects_an_unknown_preset(conn):
    with pytest.raises(LookupError, match="preset"):
        queue_setup(conn, _source(conn), 0, "nope")


def test_queue_setup_rejects_an_unknown_source(conn):
    with pytest.raises(LookupError, match="source"):
        queue_setup(conn, "nope", 0, _preset(conn))


def test_queue_setup_allows_re_running_on_a_ready_source(conn):
    source_id = _source(conn, status="ready")
    assert queue_setup(conn, source_id, 0, _preset(conn))


def test_queue_setup_refuses_a_source_mid_job(conn):
    source_id = _source(conn, status="detecting")
    with pytest.raises(RuntimeError, match="detecting"):
        queue_setup(conn, source_id, 0, _preset(conn))
