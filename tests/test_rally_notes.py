import pytest

from bootleg.db.rallies import list_rallies, replace_rallies
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


def _rows(conn, seeded):
    return list_rallies(conn, seeded["session_id"])


def test_note_defaults_to_empty_string(conn, seeded):
    # NOT NULL DEFAULT '' rather than a nullable column: every reader then
    # handles one absent-note representation instead of two.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    assert _rows(conn, seeded)[0]["note"] == ""


def test_note_carries_across_a_resegment_by_overlap(conn, seeded):
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rows(conn, seeded)[0]["id"]
    conn.execute("UPDATE rallies SET note = ? WHERE id = ?", ("late on the backhand", rally_id))
    conn.commit()

    # Same rally, boundaries nudged -- the case a threshold sweep produces.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1200, 4800, 0.8)])
    assert _rows(conn, seeded)[0]["note"] == "late on the backhand"


def test_note_is_dropped_when_nothing_overlaps_by_more_than_half(conn, seeded):
    # A rally the new threshold stops detecting takes its note with it, exactly
    # as it takes its star. Stated in the spec as a chosen consequence.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rows(conn, seeded)[0]["id"]
    conn.execute("UPDATE rallies SET note = ? WHERE id = ?", ("gone", rally_id))
    conn.commit()

    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(20_000, 24_000, 0.8)])
    assert _rows(conn, seeded)[0]["note"] == ""


def test_the_longest_overlap_wins_when_two_old_rallies_qualify(conn, seeded):
    # Two short notes, one long new span covering both. Ambiguity is resolved
    # by overlap rather than by row order, so the result does not depend on
    # what sqlite happens to return first.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 3000, 0.8), Interval(3000, 9000, 0.8)])
    rows = _rows(conn, seeded)
    conn.execute("UPDATE rallies SET note = ? WHERE id = ?", ("short one", rows[0]["id"]))
    conn.execute("UPDATE rallies SET note = ? WHERE id = ?", ("long one", rows[1]["id"]))
    conn.commit()

    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 9000, 0.8)])
    assert _rows(conn, seeded)[0]["note"] == "long one"


def test_the_earlier_start_wins_when_raw_overlaps_tie(conn, seeded):
    # Old (0, 600) and old (400, 1000) are both 600ms rallies, and a new span
    # merging them -- (0, 1000) -- overlaps each by exactly 600ms: the same
    # raw overlap, so the longest-overlap ranking alone cannot separate them.
    # This is the tie the docstring's third tie-break exists for; without it
    # the winner is whichever row sqlite's un-ordered read-back lists first.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(0, 600, 0.8), Interval(400, 1000, 0.8)])
    rows = _rows(conn, seeded)
    early_id = next(r["id"] for r in rows if r["start_ms"] == 0)
    late_id = next(r["id"] for r in rows if r["start_ms"] == 400)
    conn.execute("UPDATE rallies SET note = ? WHERE id = ?", ("earlier", early_id))
    conn.execute("UPDATE rallies SET note = ? WHERE id = ?", ("later", late_id))
    conn.commit()

    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(0, 1000, 0.8)])
    assert _rows(conn, seeded)[0]["note"] == "earlier"


def test_a_note_alone_keeps_a_rally_in_the_carry_over_read_back(conn, seeded):
    # The read-back's WHERE clause selects rows worth carrying. A rally with a
    # note but no star, no point, no rejection and no clip is one of them --
    # without the note != '' term it would not be read back at all.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rows(conn, seeded)[0]["id"]
    conn.execute("UPDATE rallies SET note = ? WHERE id = ?", ("only a note", rally_id))
    conn.commit()

    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1100, 5100, 0.8)])
    assert _rows(conn, seeded)[0]["note"] == "only a note"
