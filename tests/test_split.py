"""Cutting one rally in two, and merging a hand-made half back.

The detector merges two rallies whenever the break between them scores
above threshold -- correctly, since the tuning that separates them shreds
real rallies. Timeline mode could previously only move a merged rally's two
boundaries, so the reviewer's only options were to trim down to one half
and lose the other, or keep one oversized clip.

The half a human cuts carries NO detector span. rally_labels anchors on
(source_id, det_start_ms, det_end_ms), so two halves inheriting one det span
would collide in the corpus and the second labelled would silently overwrite
the first. See the spec, section 3.
"""
import pytest

from splitstep.db.rallies import (
    list_rallies,
    merge_into_previous,
    replace_rallies,
    set_bounds,
    set_clip_path,
    set_note,
    set_point,
    set_star,
    split_rally,
)
from splitstep.db.sessions import add_source, find_or_create_session_for_date
from splitstep.detect.segment import Interval


def _seeded(conn, name="IMG_9100.MOV"):
    session_id = find_or_create_session_for_date(conn, "2026-08-21")
    source_id, _idx = add_source(
        conn, session_id, recorded_at="2026-08-21T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name=name,
    )
    return session_id, source_id


def test_split_produces_two_abutting_halves(conn):
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    rally_id = list_rallies(conn, session_id)[0]["id"]

    new_id = split_rally(conn, rally_id, 5000)

    rows = list_rallies(conn, session_id)
    assert len(rows) == 2
    assert (rows[0]["start_ms"], rows[0]["end_ms"]) == (1000, 5000)
    assert (rows[1]["start_ms"], rows[1]["end_ms"]) == (5000, 9000)
    assert rows[1]["id"] == new_id
    # The original row survives as the first half rather than being deleted
    # and re-inserted: it is the one holding the detector's provenance.
    assert rows[0]["id"] == rally_id


def test_second_half_carries_no_detector_span(conn):
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    rally_id = list_rallies(conn, session_id)[0]["id"]

    split_rally(conn, rally_id, 5000)

    rows = list_rallies(conn, session_id)
    # First half keeps the detector's ORIGINAL span, unchanged -- not
    # narrowed to its new bounds. det_* records what the detector guessed.
    assert (rows[0]["det_start_ms"], rows[0]["det_end_ms"]) == (1000, 9000)
    assert rows[1]["det_start_ms"] is None
    assert rows[1]["det_end_ms"] is None


def test_split_inherits_every_review_flag(conn):
    # Not an arbitrary choice: this is what replace_rallies' own carry-over
    # rule produces for these two intervals. overlap_fraction divides by the
    # SHORTER span, so each half sits fully inside the parent and scores a
    # flat 1.0, clearing STAR_OVERLAP_MIN outright. A split rally must behave
    # exactly as it would had the detector proposed both intervals.
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    rally_id = list_rallies(conn, session_id)[0]["id"]
    set_star(conn, rally_id, True)
    set_point(conn, rally_id, True)
    set_note(conn, rally_id, "late backhand")

    split_rally(conn, rally_id, 5000)

    second = list_rallies(conn, session_id)[1]
    assert second["starred"] == 1
    assert second["rejected"] == 0
    assert second["point"] == 1
    assert second["note"] == "late backhand"
    assert second["confidence"] == 0.8
    assert second["seen_at"] is not None
    assert second["reviewed_at"] is not None


def test_split_nulls_clip_path_on_both_halves(conn):
    # _carried_clip_path demands an EXACT span match for exactly this reason:
    # the 4K file on disk was cut at the old span and describes neither half.
    # The orphaned file is `clips prune`'s problem -- see set_clip_path's
    # docstring, which names that command as its only consumer.
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    rally_id = list_rallies(conn, session_id)[0]["id"]
    set_clip_path(conn, rally_id, "clips/01-1000-9000.mp4")

    split_rally(conn, rally_id, 5000)

    rows = list_rallies(conn, session_id)
    assert rows[0]["clip_path"] is None
    assert rows[1]["clip_path"] is None


@pytest.mark.parametrize("at_ms", [1000, 9000, 500, 12000])
def test_split_refuses_a_cut_outside_the_rally(conn, at_ms):
    # Strictly inside, so both halves are non-empty. The MIN_RALLY_MS floor
    # is deliberately NOT enforced here -- /bounds validates only
    # end_ms > start_ms and leaves the floor to clampMinGap client-side, and
    # media/concat.py states outright that a hand-trimmed clip well under
    # 1.5s is a real input. The server rejects the incoherent, not the tiny.
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    rally_id = list_rallies(conn, session_id)[0]["id"]

    with pytest.raises(ValueError):
        split_rally(conn, rally_id, at_ms)

    assert len(list_rallies(conn, session_id)) == 1


def test_split_refuses_an_unknown_rally(conn):
    _seeded(conn)
    with pytest.raises(ValueError):
        split_rally(conn, "nope", 5000)


def test_split_renumbers_across_two_sources(conn):
    # The case _renumber's two-phase negative-placeholder dance exists for:
    # growing a NON-LAST source's rally count walks straight into the next
    # source's still-live idx under UNIQUE(session_id, idx).
    session_id, src_a = _seeded(conn, "IMG_9100.MOV")
    src_b, _idx = add_source(
        conn, session_id, recorded_at="2026-08-21T11:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9101.MOV",
    )
    replace_rallies(conn, session_id, src_a,
                    [Interval(1000, 9000, 0.8), Interval(20000, 25000, 0.7)])
    replace_rallies(conn, session_id, src_b, [Interval(1000, 4000, 0.9)])
    first_of_a = list_rallies(conn, session_id)[0]["id"]

    split_rally(conn, first_of_a, 5000)

    rows = list_rallies(conn, session_id)
    assert [r["idx"] for r in rows] == [1, 2, 3, 4]
    # Ordered by source idx, then start_ms -- the new half lands second, and
    # source B's rally is pushed from 3 to 4 rather than colliding with it.
    assert [(r["source_id"], r["start_ms"]) for r in rows] == [
        (src_a, 1000), (src_a, 5000), (src_a, 20000), (src_b, 1000),
    ]


def test_split_leaves_the_set_untouched_when_it_fails(conn, monkeypatch):
    # One transaction. A half-applied split -- new row inserted, renumber
    # never run -- would sit on the shared connection violating
    # UNIQUE(session_id, idx) until some unrelated later commit persisted it.
    import splitstep.db.rallies as rallies_mod

    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    rally_id = list_rallies(conn, session_id)[0]["id"]

    def boom(*_args, **_kwargs):
        raise RuntimeError("renumber exploded")

    monkeypatch.setattr(rallies_mod, "_renumber", boom)
    with pytest.raises(RuntimeError):
        split_rally(conn, rally_id, 5000)

    rows = list_rallies(conn, session_id)
    assert len(rows) == 1
    assert (rows[0]["start_ms"], rows[0]["end_ms"]) == (1000, 9000)


def test_merge_puts_a_split_rally_back_together(conn):
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    rally_id = list_rallies(conn, session_id)[0]["id"]
    new_id = split_rally(conn, rally_id, 5000)

    merge_into_previous(conn, new_id)

    rows = list_rallies(conn, session_id)
    assert len(rows) == 1
    assert (rows[0]["id"], rows[0]["start_ms"], rows[0]["end_ms"]) == (rally_id, 1000, 9000)
    # Merging back does not restore provenance to a rally that never lost
    # it: the survivor's det span is the one it always had.
    assert (rows[0]["det_start_ms"], rows[0]["det_end_ms"]) == (1000, 9000)
    assert rows[0]["idx"] == 1


def test_merge_nulls_the_survivors_clip_path(conn):
    # Same reason split does: the row's span just changed, so a file cut at
    # the old span no longer describes it.
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    rally_id = list_rallies(conn, session_id)[0]["id"]
    new_id = split_rally(conn, rally_id, 5000)
    set_clip_path(conn, rally_id, "clips/01-1000-5000.mp4")

    merge_into_previous(conn, new_id)

    assert list_rallies(conn, session_id)[0]["clip_path"] is None


def test_merge_refuses_a_rally_carrying_a_detector_span(conn):
    # The whole safety story. Merge can only ever undo something a human
    # made; it must never delete a row the label corpus is anchored to.
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(5000, 9000, 0.7)])
    second = list_rallies(conn, session_id)[1]["id"]

    with pytest.raises(ValueError):
        merge_into_previous(conn, second)

    assert len(list_rallies(conn, session_id)) == 2


def test_merge_refuses_a_non_abutting_predecessor(conn):
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    rally_id = list_rallies(conn, session_id)[0]["id"]
    new_id = split_rally(conn, rally_id, 5000)
    # Trim the first half's tail, opening a gap. The two rows no longer
    # describe one contiguous stretch of footage, so rejoining them would
    # invent play across the gap.
    set_bounds(conn, rally_id, 1000, 4000)

    with pytest.raises(ValueError):
        merge_into_previous(conn, new_id)


def test_merge_refuses_when_the_previous_rally_is_another_source(conn):
    session_id, src_a = _seeded(conn, "IMG_9100.MOV")
    src_b, _idx = add_source(
        conn, session_id, recorded_at="2026-08-21T11:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9101.MOV",
    )
    replace_rallies(conn, session_id, src_a, [Interval(1000, 5000, 0.8)])
    replace_rallies(conn, session_id, src_b, [Interval(5000, 9000, 0.7)])
    # Hand-make a det-less first rally on source B so the det guard passes
    # and the source guard is what has to refuse.
    b_first = list_rallies(conn, session_id)[1]["id"]
    conn.execute(
        "UPDATE rallies SET det_start_ms = NULL, det_end_ms = NULL WHERE id = ?",
        (b_first,),
    )
    conn.commit()

    with pytest.raises(ValueError):
        merge_into_previous(conn, b_first)


def test_merge_refuses_an_unknown_rally(conn):
    _seeded(conn)
    with pytest.raises(ValueError):
        merge_into_previous(conn, "nope")


def test_a_twice_split_rally_collapses_in_reverse(conn):
    # Nested splits fall out of the rules rather than needing a case of
    # their own: split_rally never reads det_*, and merge tests the TARGET's
    # det span, not the previous rally's. The one row carrying provenance is
    # never a legal merge target at any step.
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    original = list_rallies(conn, session_id)[0]["id"]
    second = split_rally(conn, original, 5000)
    third = split_rally(conn, second, 7000)

    assert len(list_rallies(conn, session_id)) == 3

    merge_into_previous(conn, third)
    merge_into_previous(conn, second)

    rows = list_rallies(conn, session_id)
    assert len(rows) == 1
    assert (rows[0]["id"], rows[0]["start_ms"], rows[0]["end_ms"]) == (original, 1000, 9000)

    # And the one that still has provenance stays un-mergeable.
    with pytest.raises(ValueError):
        merge_into_previous(conn, original)
