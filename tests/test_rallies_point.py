import pytest

from bootleg.db.rallies import list_rallies, replace_rallies, set_point, set_star
from bootleg.db.sessions import add_source, find_or_create_session_for_date
from bootleg.detect.segment import Interval


@pytest.fixture
def seeded(conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, _idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    return {"session_id": session_id, "source_id": source_id}


def _rallies(conn, session_id):
    return list_rallies(conn, session_id)


def test_point_defaults_to_zero(conn, seeded):
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    assert _rallies(conn, seeded["session_id"])[0]["point"] == 0


def test_set_point_toggles_and_stamps_reviewed_at(conn, seeded):
    # reviewed_at records that a human has ruled on this rally at all.
    # Marking a point is such a ruling -- otherwise a reviewer who marks every
    # point of a tiebreaker and stars none ends with a session that still reads
    # unreviewed.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rallies(conn, seeded["session_id"])[0]["id"]

    set_point(conn, rally_id, True)
    row = _rallies(conn, seeded["session_id"])[0]
    assert row["point"] == 1
    assert row["reviewed_at"] is not None

    set_point(conn, rally_id, False)
    assert _rallies(conn, seeded["session_id"])[0]["point"] == 0


def test_set_point_does_not_move_reviewed_at_once_set(conn, seeded):
    # COALESCE, matching set_star/set_rejected: the first ruling is the one
    # that counts, so re-marking does not rewrite when review happened.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rallies(conn, seeded["session_id"])[0]["id"]
    set_point(conn, rally_id, True)
    first = _rallies(conn, seeded["session_id"])[0]["reviewed_at"]
    set_point(conn, rally_id, False)
    assert _rallies(conn, seeded["session_id"])[0]["reviewed_at"] == first


def test_point_is_independent_of_starred(conn, seeded):
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rallies(conn, seeded["session_id"])[0]["id"]
    set_point(conn, rally_id, True)
    set_star(conn, rally_id, True)
    row = _rallies(conn, seeded["session_id"])[0]
    assert (row["point"], row["starred"]) == (1, 1)


def test_replace_rallies_carries_point_across_a_resegment(conn, seeded):
    """The test that stops a threshold sweep wiping every point mark.

    replace_rallies deletes and rebuilds every rally for a source. starred and
    rejected have always been carried across by >50% overlap; point must be
    too, or the first re-segment silently discards the reviewer's entire
    tiebreaker.
    """
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rallies(conn, seeded["session_id"])[0]["id"]
    set_point(conn, rally_id, True)

    # A sweep that shifts the boundaries slightly -- still clearly the same rally.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1200, 4800, 0.7)])

    assert _rallies(conn, seeded["session_id"])[0]["point"] == 1


def test_a_new_rally_that_overlaps_nothing_is_not_a_point(conn, seeded):
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rallies(conn, seeded["session_id"])[0]["id"]
    set_point(conn, rally_id, True)

    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1200, 4800, 0.7), Interval(60_000, 66_000, 0.6)])
    rows = _rallies(conn, seeded["session_id"])
    assert [r["point"] for r in rows] == [1, 0]


def test_a_rejected_carry_over_does_not_gain_a_point(conn, seeded):
    # rejected takes priority over starred in the existing carry-over; point is
    # orthogonal to both and must not be invented for a segment that only ever
    # overlapped a rejection.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    from bootleg.db.rallies import set_rejected
    set_rejected(conn, _rallies(conn, seeded["session_id"])[0]["id"], True)
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1200, 4800, 0.7)])
    row = _rallies(conn, seeded["session_id"])[0]
    assert (row["rejected"], row["point"]) == (1, 0)
