"""seen_at vs reviewed_at.

reviewed_at means "a human ruled on this rally" (star/point/reject) and
drives session status alone -- see refresh_session_review_status. seen_at
means "a human has looked at this rally at all", including a plain
right-arrow skip that renders no verdict. Splitting them is the fix for the
review queue always reopening on the same rally: persist.ts's skip case used
to write nothing, so a skipped-but-unjudged rally kept reviewed_at NULL
forever and every reopened queue landed back on it (queue.ts's firstUnseen
used reviewed_at to find where to resume).

These tests cover the db-layer half: set_seen itself, and that the three
judgement writers (set_star/set_point/set_rejected) stamp seen_at alongside
reviewed_at through the same COALESCE, so a rally that was starred but never
skipped still reads as seen.
"""
from bootleg.db.rallies import (
    list_rallies,
    replace_rallies,
    set_point,
    set_rejected,
    set_seen,
    set_star,
)
from bootleg.db.sessions import add_source, find_or_create_session_for_date
from bootleg.detect.segment import Interval


def _rallies(conn, session_id):
    return list_rallies(conn, session_id)


def _seeded(conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-21")
    source_id, _idx = add_source(
        conn, session_id, recorded_at="2026-08-21T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9100.MOV",
    )
    return {"session_id": session_id, "source_id": source_id}


def test_seen_at_defaults_to_none(conn):
    seeded = _seeded(conn)
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    assert _rallies(conn, seeded["session_id"])[0]["seen_at"] is None


def test_set_seen_stamps_seen_at_but_not_reviewed_at(conn):
    # This is the skip path: a rally arrowed past but never starred/pointed/
    # rejected must end up seen without becoming a ruling nobody made.
    seeded = _seeded(conn)
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rallies(conn, seeded["session_id"])[0]["id"]

    set_seen(conn, rally_id)

    row = _rallies(conn, seeded["session_id"])[0]
    assert row["seen_at"] is not None
    assert row["reviewed_at"] is None


def test_set_seen_does_not_move_seen_at_once_set(conn):
    # COALESCE, same shape as mark_reviewed: the first write wins, so a
    # rally revisited later in the pass does not have its "first seen"
    # timestamp overwritten.
    seeded = _seeded(conn)
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rallies(conn, seeded["session_id"])[0]["id"]

    set_seen(conn, rally_id)
    first = _rallies(conn, seeded["session_id"])[0]["seen_at"]
    set_seen(conn, rally_id)
    assert _rallies(conn, seeded["session_id"])[0]["seen_at"] == first


def test_set_star_also_stamps_seen_at(conn):
    # A starred rally was necessarily looked at -- without this, a rally
    # starred on the very first pass (never skipped) would still read as
    # unseen and the queue would return to it.
    seeded = _seeded(conn)
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rallies(conn, seeded["session_id"])[0]["id"]

    set_star(conn, rally_id, True)

    row = _rallies(conn, seeded["session_id"])[0]
    assert row["seen_at"] is not None
    assert row["reviewed_at"] is not None


def test_set_point_also_stamps_seen_at(conn):
    seeded = _seeded(conn)
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rallies(conn, seeded["session_id"])[0]["id"]

    set_point(conn, rally_id, True)

    assert _rallies(conn, seeded["session_id"])[0]["seen_at"] is not None


def test_set_rejected_also_stamps_seen_at(conn):
    seeded = _seeded(conn)
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rallies(conn, seeded["session_id"])[0]["id"]

    set_rejected(conn, rally_id, True)

    assert _rallies(conn, seeded["session_id"])[0]["seen_at"] is not None


def test_starring_seen_at_is_not_moved_by_a_later_seen_call(conn):
    # COALESCE on both writers targets the same column: whichever of
    # set_star/set_seen runs first records the "first seen" instant, and the
    # other must not clobber it.
    seeded = _seeded(conn)
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rallies(conn, seeded["session_id"])[0]["id"]

    set_star(conn, rally_id, True)
    first = _rallies(conn, seeded["session_id"])[0]["seen_at"]
    set_seen(conn, rally_id)
    assert _rallies(conn, seeded["session_id"])[0]["seen_at"] == first
