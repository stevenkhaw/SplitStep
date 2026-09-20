"""Adding a rally at a span the detector never proposed.

The detector cannot propose what it never scored -- pair mode hard-zeroes
any frame missing a player -- so play it missed is otherwise unreachable:
`split` cuts an existing rally in two and `merge` undoes that, but neither
can reach a stretch of footage no rally covers.

The row is the same kind `split_rally` already makes: det_start_ms and
det_end_ms NULL, which is the "made by a human, not proposed by the
detector" marker. Every documented consequence of that absence -- merge
accepts it, /label refuses it, LabelController filters it out, a re-segment
destroys it -- is inherited for free rather than re-decided here. See the
spec, part 2.
"""
import pytest

from splitstep.db.rallies import MIN_RALLY_MS, create_rally, list_rallies, replace_rallies
from splitstep.db.sessions import add_source, find_or_create_session_for_date
from splitstep.detect.segment import Interval

DURATION_MS = 600_000


def _seeded(conn, name="IMG_9100.MOV"):
    session_id = find_or_create_session_for_date(conn, "2026-09-19")
    source_id, _idx = add_source(
        conn, session_id, recorded_at="2026-09-19T10:00:00Z", duration_ms=DURATION_MS,
        width=3840, height=2160, fps=30.0, original_name=name,
    )
    return session_id, source_id


def test_the_new_rally_carries_no_detector_span(conn):
    # The whole point of the feature: the absence of det_* is what marks a
    # rally as human-made, and every consumer already asks that question.
    session_id, source_id = _seeded(conn)

    new_id = create_rally(conn, source_id, 20_000, 26_000)

    rows = list_rallies(conn, session_id)
    assert [r["id"] for r in rows] == [new_id]
    assert (rows[0]["start_ms"], rows[0]["end_ms"]) == (20_000, 26_000)
    assert rows[0]["det_start_ms"] is None
    assert rows[0]["det_end_ms"] is None


def test_the_new_rally_starts_plain(conn):
    # No default asserts a judgement nobody made. Flagging it is the same
    # keystrokes as any other rally.
    session_id, source_id = _seeded(conn)

    create_rally(conn, source_id, 20_000, 26_000)

    row = list_rallies(conn, session_id)[0]
    assert row["starred"] == 0
    assert row["rejected"] == 0
    assert row["point"] == 0
    assert row["winner"] == ""
    assert row["note"] == ""
    assert row["clip_path"] is None
    assert row["reviewed_at"] is None


def test_idx_places_it_in_time_order_among_its_neighbours(conn):
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 9000, 0.8), Interval(30_000, 36_000, 0.7)])

    new_id = create_rally(conn, source_id, 12_000, 18_000)

    rows = list_rallies(conn, session_id)
    assert [r["idx"] for r in rows] == [1, 2, 3]
    assert [r["start_ms"] for r in rows] == [1000, 12_000, 30_000]
    assert rows[1]["id"] == new_id


def test_idx_renumbers_across_two_sources(conn):
    # _renumber's two-phase dance: growing a NON-LAST source's rally count
    # walks into the next source's still-live idx under UNIQUE(session_id, idx).
    session_id, src_a = _seeded(conn, "IMG_9100.MOV")
    src_b, _idx = add_source(
        conn, session_id, recorded_at="2026-09-19T11:00:00Z", duration_ms=DURATION_MS,
        width=3840, height=2160, fps=30.0, original_name="IMG_9101.MOV",
    )
    replace_rallies(conn, session_id, src_a, [Interval(1000, 9000, 0.8)])
    replace_rallies(conn, session_id, src_b, [Interval(1000, 4000, 0.9)])

    create_rally(conn, src_a, 12_000, 18_000)

    rows = list_rallies(conn, session_id)
    assert [r["idx"] for r in rows] == [1, 2, 3]
    assert [(r["source_id"], r["start_ms"]) for r in rows] == [
        (src_a, 1000), (src_a, 12_000), (src_b, 1000),
    ]


@pytest.mark.parametrize("start_ms,end_ms", [
    (20_000, 20_000 + MIN_RALLY_MS - 1),
    (20_000, 20_000),
    (20_000, 19_000),
])
def test_a_too_short_span_is_refused(conn, start_ms, end_ms):
    session_id, source_id = _seeded(conn)

    with pytest.raises(ValueError):
        create_rally(conn, source_id, start_ms, end_ms)

    assert list_rallies(conn, session_id) == []


@pytest.mark.parametrize("start_ms,end_ms", [
    (DURATION_MS - 1000, DURATION_MS + 1000),
    (DURATION_MS, DURATION_MS + 5000),
    (-1000, 5000),
])
def test_a_span_outside_the_source_is_refused(conn, start_ms, end_ms):
    session_id, source_id = _seeded(conn)

    with pytest.raises(ValueError):
        create_rally(conn, source_id, start_ms, end_ms)

    assert list_rallies(conn, session_id) == []


def test_a_span_ending_exactly_at_the_duration_is_accepted(conn):
    # The bound is inclusive at both ends: end_ms is exclusive as a span, so
    # a rally running to the last frame of the file is ordinary, not an
    # overrun.
    session_id, source_id = _seeded(conn)

    create_rally(conn, source_id, DURATION_MS - 5000, DURATION_MS)

    assert len(list_rallies(conn, session_id)) == 1


def test_an_unknown_source_is_refused(conn):
    _seeded(conn)
    with pytest.raises(ValueError):
        create_rally(conn, "nope", 1000, 5000)


def test_an_overlapping_span_is_allowed_and_both_rallies_survive(conn):
    # Deliberately no collision check. The rally list is not a partition;
    # clip_relpath is span-derived so two overlapping rallies name two
    # different files, and score replay orders by idx, which _renumber
    # assigns regardless.
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(10_000, 20_000, 0.8)])
    existing = list_rallies(conn, session_id)[0]["id"]

    new_id = create_rally(conn, source_id, 15_000, 25_000)

    rows = list_rallies(conn, session_id)
    assert len(rows) == 2
    assert {r["id"] for r in rows} == {existing, new_id}
    assert [(r["idx"], r["start_ms"], r["end_ms"]) for r in rows] == [
        (1, 10_000, 20_000), (2, 15_000, 25_000),
    ]


def test_a_failed_create_leaves_the_set_untouched(conn, monkeypatch):
    # One transaction, like split_rally: a half-applied insert -- new row in,
    # renumber never run -- would sit on the shared connection violating
    # UNIQUE(session_id, idx) until some unrelated later commit persisted it.
    import splitstep.db.rallies as rallies_mod

    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])

    def boom(*_args, **_kwargs):
        raise RuntimeError("renumber exploded")

    monkeypatch.setattr(rallies_mod, "_renumber", boom)
    with pytest.raises(RuntimeError):
        create_rally(conn, source_id, 12_000, 18_000)

    rows = list_rallies(conn, session_id)
    assert len(rows) == 1
    assert (rows[0]["start_ms"], rows[0]["end_ms"]) == (1000, 9000)
