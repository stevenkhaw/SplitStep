import json

import pytest

from splitstep.db.presets import create_preset
from splitstep.db.schema import connect, migrate
from splitstep.db.sessions import (
    add_source,
    find_or_create_session_for_date,
    get_source,
    set_source_status,
)
from splitstep.detect.geometry import Quad
from splitstep.setup import queue_setup


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
    # create_preset takes a Quad, not raw points -- see splitstep/db/presets.py.
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


def test_queue_setup_illegal_rotation_with_unknown_preset_raises_ValueError(conn):
    """Validation order: rotation (caller's input) checked before preset (DB lookup)."""
    source_id = _source(conn)
    with pytest.raises(ValueError, match="0, 90, 180 or 270"):
        queue_setup(conn, source_id, 45, "nope")


def test_queue_setup_illegal_rotation_leaves_source_unchanged_and_queues_nothing(conn):
    """A failed rotation validation must not write any columns or queue a job."""
    source_id = _source(conn)
    original_row = get_source(conn, source_id)
    original_rotation = original_row["rotation_deg"]
    original_preset = original_row["court_preset_id"]
    original_job_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    with pytest.raises(ValueError):
        queue_setup(conn, source_id, 45, _preset(conn))

    row = get_source(conn, source_id)
    assert row["rotation_deg"] == original_rotation
    assert row["court_preset_id"] == original_preset
    new_job_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert new_job_count == original_job_count


def test_queue_setup_unknown_preset_leaves_source_unchanged_and_queues_nothing(conn):
    """A failed preset validation must not write any columns or queue a job."""
    source_id = _source(conn)
    original_row = get_source(conn, source_id)
    original_rotation = original_row["rotation_deg"]
    original_preset = original_row["court_preset_id"]
    original_job_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    with pytest.raises(LookupError, match="preset"):
        queue_setup(conn, source_id, 90, "nope")

    row = get_source(conn, source_id)
    assert row["rotation_deg"] == original_rotation
    assert row["court_preset_id"] == original_preset
    new_job_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert new_job_count == original_job_count


# -- mount, format and court corners ----------------------------------------

CORNERS = [(0.2, 0.6), (0.9, 0.6), (0.6, 0.45), (0.45, 0.45)]
CROSSED = [(0.9, 0.6), (0.2, 0.6), (0.6, 0.45), (0.45, 0.45)]
FENCE_SENTENCE = "Fence + Singles needs the four court corners — draw them on the court lines."


def _jobs(conn):
    return conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


def test_queue_setup_refuses_fence_singles_without_corners(conn):
    source_id, preset_id = _source(conn), _preset(conn)
    with pytest.raises(ValueError) as exc:
        queue_setup(conn, source_id, 90, preset_id, mount="fence", play_format="singles")
    assert str(exc.value) == FENCE_SENTENCE
    row = get_source(conn, source_id)
    assert row["rotation_deg"] != 90 and row["mount"] == "" and row["court_preset_id"] is None
    assert _jobs(conn) == 0


def test_queue_setup_refuses_crossed_corners(conn):
    from splitstep.detect.court import validate_corners

    source_id, preset_id = _source(conn), _preset(conn)
    with pytest.raises(ValueError) as exc:
        validate_corners(CROSSED)
    sentence = str(exc.value)
    with pytest.raises(ValueError) as got:
        queue_setup(conn, source_id, 0, preset_id, mount="fence", play_format="singles",
                    court_corners=CROSSED)
    assert str(got.value) == sentence
    assert _jobs(conn) == 0


def test_queue_setup_stores_choices_and_enqueues(conn):
    source_id, preset_id = _source(conn), _preset(conn)
    job_id = queue_setup(conn, source_id, 0, preset_id, mount="fence", play_format="singles",
                         court_corners=CORNERS)
    row = get_source(conn, source_id)
    assert (row["mount"], row["play_format"]) == ("fence", "singles")
    assert json.loads(row["court_corners"]) == [list(p) for p in CORNERS]
    assert conn.execute("SELECT type FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == (
        "build_proxy")


def test_queue_setup_keeps_stored_choices_when_omitted(conn):
    source_id, preset_id = _source(conn), _preset(conn)
    queue_setup(conn, source_id, 0, preset_id, mount="fence", play_format="singles",
                court_corners=CORNERS)
    queue_setup(conn, source_id, 180, preset_id)
    row = get_source(conn, source_id)
    assert row["rotation_deg"] == 180
    assert (row["mount"], row["play_format"]) == ("fence", "singles")
    assert row["court_corners"] is not None
    # An explicit '' does write.
    queue_setup(conn, source_id, 180, preset_id, mount="", play_format="", court_corners=None)
    row = get_source(conn, source_id)
    assert (row["mount"], row["play_format"], row["court_corners"]) == ("", "", None)


def test_queue_setup_derives_the_region_from_corners(conn):
    from splitstep.db.presets import get_preset

    source_id = _source(conn)
    queue_setup(conn, source_id, 0, None, mount="fence", play_format="doubles",
                court_corners=CORNERS)
    preset = get_preset(conn, get_source(conn, source_id)["court_preset_id"])
    pts = json.loads(preset["quad"])
    assert any(not (0.0 <= c <= 1.0) for p in pts for c in p)


def test_queue_setup_without_preset_or_corners_is_refused(conn):
    with pytest.raises(ValueError):
        queue_setup(conn, _source(conn), 0, None)


def test_apply_choices_writes_without_a_job_or_preset(conn):
    from splitstep.setup import apply_choices

    source_id, preset_id = _source(conn, "ready"), _preset(conn)
    queue_setup(conn, source_id, 0, preset_id)
    set_source_status(conn, source_id, "ready")
    before = _jobs(conn)
    apply_choices(conn, source_id, mount="fence", play_format="singles", court_corners=CORNERS)
    row = get_source(conn, source_id)
    assert row["mount"] == "fence" and row["court_preset_id"] == preset_id
    assert _jobs(conn) == before
    # Format alone, judged against the stored corners.
    apply_choices(conn, source_id, play_format="doubles")
    assert get_source(conn, source_id)["court_corners"] is not None


def test_apply_choices_refuses_fence_singles_without_corners_and_a_busy_source(conn):
    from splitstep.setup import apply_choices

    source_id = _source(conn, "ready")
    with pytest.raises(ValueError) as exc:
        apply_choices(conn, source_id, mount="fence", play_format="singles")
    assert str(exc.value) == FENCE_SENTENCE
    set_source_status(conn, source_id, "detecting")
    with pytest.raises(RuntimeError):
        apply_choices(conn, source_id, mount="ground")
    with pytest.raises(LookupError):
        apply_choices(conn, "nope")


def test_a_redrawn_court_forgets_the_detected_scorer_but_a_resave_does_not(conn):
    from splitstep.db.sessions import set_source_scorer
    from splitstep.setup import apply_choices

    source_id, preset_id = _source(conn, "ready"), _preset(conn)
    queue_setup(conn, source_id, 0, preset_id, mount="fence", play_format="singles",
                court_corners=CORNERS)
    set_source_status(conn, source_id, "ready")
    set_source_scorer(conn, source_id, "fence")

    def detected():
        return get_source(conn, source_id)["detected_scorer"]

    # Identical corners, and omitted corners, are not a change.
    apply_choices(conn, source_id, court_corners=[list(p) for p in CORNERS])
    apply_choices(conn, source_id, play_format="doubles")
    assert detected() == "fence"
    # A moved corner is.
    moved = [(CORNERS[0][0] + 0.01, CORNERS[0][1]), *CORNERS[1:]]
    apply_choices(conn, source_id, court_corners=moved)
    assert detected() is None
    # queue_setup is the second write site.
    set_source_scorer(conn, source_id, "fence")
    queue_setup(conn, source_id, 0, preset_id, court_corners=moved)
    assert detected() == "fence"
    set_source_status(conn, source_id, "ready")
    queue_setup(conn, source_id, 0, preset_id, court_corners=CORNERS)
    assert detected() is None
