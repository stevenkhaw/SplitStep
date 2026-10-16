import json

import pytest

from splitstep.db.sessions import (
    add_source,
    find_or_create_session_for_date,
    latest_setup_choices,
    set_source_scorer,
    set_source_setup,
)

CORNERS = [(0.1, 0.2), (0.9, 0.2), (0.95, 0.8), (0.05, 0.8)]


def _source(conn, recorded_at, day="2026-10-12"):
    session_id = find_or_create_session_for_date(conn, day)
    source_id, _ = add_source(
        conn, session_id, recorded_at=recorded_at, duration_ms=60_000,
        width=1920, height=1080, fps=30.0, original_name="IMG.MOV",
    )
    return source_id


def _row(conn, source_id):
    return conn.execute(
        "SELECT mount, play_format, court_corners, detected_scorer, rotation_deg,"
        " court_preset_id FROM sources WHERE id=?",
        (source_id,),
    ).fetchone()


def test_new_columns_default_to_not_set(conn):
    row = _row(conn, _source(conn, "2026-10-12T10:00:00Z"))
    assert row["mount"] == ""
    assert row["play_format"] == ""
    assert row["court_corners"] is None
    assert row["detected_scorer"] is None


def test_set_source_setup_writes_mount_format_corners(conn):
    sid = _source(conn, "2026-10-12T10:00:00Z")
    set_source_setup(conn, sid, 90, None, mount="fence", play_format="singles",
                     court_corners=CORNERS)
    row = _row(conn, sid)
    assert (row["mount"], row["play_format"], row["rotation_deg"]) == ("fence", "singles", 90)
    assert json.loads(row["court_corners"]) == [list(p) for p in CORNERS]


def test_set_source_setup_old_call_shape_still_works(conn):
    sid = _source(conn, "2026-10-12T10:00:00Z")
    set_source_setup(conn, sid, 0, None)
    row = _row(conn, sid)
    assert (row["mount"], row["play_format"], row["court_corners"]) == ("", "", None)


@pytest.mark.parametrize("kw", [{"mount": "tripod"}, {"play_format": "mixed"}])
def test_set_source_setup_rejects_unknown_choice(conn, kw):
    sid = _source(conn, "2026-10-12T10:00:00Z")
    with pytest.raises(ValueError):
        set_source_setup(conn, sid, 90, None, **kw)
    row = _row(conn, sid)
    assert row["rotation_deg"] == 0 and row["mount"] == ""


def test_set_source_scorer(conn):
    sid = _source(conn, "2026-10-12T10:00:00Z")
    set_source_scorer(conn, sid, "fence")
    assert _row(conn, sid)["detected_scorer"] == "fence"
    with pytest.raises(ValueError):
        set_source_scorer(conn, sid, "bogus")


def test_latest_setup_choices_empty_when_none(conn):
    _source(conn, "2026-10-12T10:00:00Z")
    assert latest_setup_choices(conn) == {"mount": "", "play_format": ""}


def test_latest_setup_choices_takes_newest_non_empty(conn):
    old = _source(conn, "2026-10-10T10:00:00Z", "2026-10-10")
    new = _source(conn, "2026-10-12T10:00:00Z", "2026-10-12")
    set_source_setup(conn, old, 0, None, mount="fence", play_format="singles")
    set_source_setup(conn, new, 0, None, mount="", play_format="doubles")
    assert latest_setup_choices(conn) == {"mount": "fence", "play_format": "doubles"}
