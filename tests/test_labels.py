import sqlite3

import pytest

from bootleg.db.labels import (
    add_label,
    format_flags,
    latest_labels,
    parse_flags,
    record_boundary_correction,
)
from bootleg.db.rallies import replace_rallies
from bootleg.db.sessions import add_source, find_or_create_session_for_date
from bootleg.detect.segment import Interval


@pytest.fixture
def seeded(conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, _idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=1920, height=1080, fps=30.0, original_name="IMG_9000.MOV",
    )
    return {"session_id": session_id, "source_id": source_id}


def test_format_flags_uses_canonical_order_regardless_of_input_order(seeded):
    assert format_flags(["end_late", "start_early"]) == "start_early,end_late"
    assert format_flags([]) == ""


def test_format_flags_rejects_an_unknown_flag(seeded):
    with pytest.raises(ValueError, match="unknown boundary flag"):
        format_flags(["start_slightly_early"])


def test_parse_flags_round_trips_and_maps_empty_to_no_flags(seeded):
    assert parse_flags("start_early,end_late") == ["start_early", "end_late"]
    assert parse_flags("") == []


def test_add_label_stores_a_verdict_row(conn, seeded):
    add_label(conn, source_id=seeded["source_id"], span_start_ms=1000,
              span_end_ms=5000, verdict="clean")
    rows = latest_labels(conn, seeded["source_id"])
    assert len(rows) == 1
    assert rows[0]["verdict"] == "clean"
    assert rows[0]["boundary_flags"] == ""
    assert rows[0]["true_start_ms"] is None


def test_add_label_rejects_an_unknown_verdict(conn, seeded):
    with pytest.raises(ValueError, match="unknown verdict"):
        add_label(conn, source_id=seeded["source_id"], span_start_ms=1000,
                  span_end_ms=5000, verdict="probably")


def test_add_label_rejects_a_row_carrying_neither_verdict_nor_corrected_span(conn, seeded):
    # The table-level CHECK. Unreachable from HTTP (the route requires a
    # verdict) but a programming error here should fail loudly, not insert junk.
    with pytest.raises(sqlite3.IntegrityError):
        add_label(conn, source_id=seeded["source_id"], span_start_ms=1000,
                  span_end_ms=5000, verdict=None)


def test_relabelling_a_span_appends_and_the_latest_row_wins(conn, seeded):
    add_label(conn, source_id=seeded["source_id"], span_start_ms=1000,
              span_end_ms=5000, verdict="not_play")
    add_label(conn, source_id=seeded["source_id"], span_start_ms=1000,
              span_end_ms=5000, verdict="clean")

    # Both rows survive -- clip #1's hand label was wrong once and the
    # correction was itself the finding, so history is never overwritten.
    total = conn.execute("SELECT COUNT(*) FROM rally_labels").fetchone()[0]
    assert total == 2

    rows = latest_labels(conn, seeded["source_id"])
    assert len(rows) == 1
    assert rows[0]["verdict"] == "clean"


def test_latest_labels_breaks_a_labelled_at_tie_by_insertion_order(conn, seeded):
    # Two labels written inside the same clock tick must still resolve
    # deterministically to the later insert, not to whichever row sqlite
    # happens to return first.
    conn.execute(
        "INSERT INTO rally_labels (id,source_id,span_start_ms,span_end_ms,verdict,"
        "boundary_flags,labelled_at) VALUES ('a',?,1000,5000,'not_play','','T')",
        (seeded["source_id"],),
    )
    conn.execute(
        "INSERT INTO rally_labels (id,source_id,span_start_ms,span_end_ms,verdict,"
        "boundary_flags,labelled_at) VALUES ('b',?,1000,5000,'clean','','T')",
        (seeded["source_id"],),
    )
    conn.commit()
    rows = latest_labels(conn, seeded["source_id"])
    assert [r["verdict"] for r in rows] == ["clean"]


def test_distinct_spans_each_keep_their_own_latest_row(conn, seeded):
    add_label(conn, source_id=seeded["source_id"], span_start_ms=1000,
              span_end_ms=5000, verdict="clean")
    add_label(conn, source_id=seeded["source_id"], span_start_ms=9000,
              span_end_ms=14000, verdict="not_play")
    rows = latest_labels(conn, seeded["source_id"])
    assert [(r["span_start_ms"], r["verdict"]) for r in rows] == [
        (1000, "clean"), (9000, "not_play")
    ]


def test_the_corpus_survives_replace_rallies(conn, seeded):
    """The load-bearing property. Approach B exists entirely for this.

    If anyone ever adds `REFERENCES rallies(id) ON DELETE CASCADE` to
    rally_labels.rally_id, replace_rallies' DELETE will wipe the corpus and
    this test is what catches it.
    """
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = conn.execute("SELECT id FROM rallies").fetchone()["id"]
    add_label(conn, source_id=seeded["source_id"], span_start_ms=1000,
              span_end_ms=5000, verdict="clean", rally_id=rally_id)

    # A threshold sweep: every rally row for the source is deleted and rebuilt.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1200, 4800, 0.7), Interval(20000, 26000, 0.6)])

    rows = latest_labels(conn, seeded["source_id"])
    assert len(rows) == 1
    assert rows[0]["verdict"] == "clean"
    assert rows[0]["span_start_ms"] == 1000


def test_record_boundary_correction_derives_start_early_and_end_late(conn, seeded):
    # Detector opened at 1000 but play began at 1400 -> it started early.
    # Detector closed at 5000 but play ended at 4600 -> it ran on, i.e. late.
    label_id = record_boundary_correction(
        conn, rally_id="r1", source_id=seeded["source_id"],
        det_start_ms=1000, det_end_ms=5000,
        true_start_ms=1400, true_end_ms=4600,
    )
    assert label_id is not None
    row = latest_labels(conn, seeded["source_id"])[0]
    assert row["verdict"] is None
    assert parse_flags(row["boundary_flags"]) == ["start_early", "end_late"]
    assert (row["true_start_ms"], row["true_end_ms"]) == (1400, 4600)


def test_record_boundary_correction_derives_start_late_and_end_early(conn, seeded):
    # The mirror case: detector opened after play began and closed before it ended.
    record_boundary_correction(
        conn, rally_id="r1", source_id=seeded["source_id"],
        det_start_ms=1000, det_end_ms=5000,
        true_start_ms=600, true_end_ms=5400,
    )
    row = latest_labels(conn, seeded["source_id"])[0]
    assert parse_flags(row["boundary_flags"]) == ["start_late", "end_early"]


def test_record_boundary_correction_writes_nothing_when_the_span_is_unchanged(conn, seeded):
    # A drag that went nowhere is not a correction.
    label_id = record_boundary_correction(
        conn, rally_id="r1", source_id=seeded["source_id"],
        det_start_ms=1000, det_end_ms=5000,
        true_start_ms=1000, true_end_ms=5000,
    )
    assert label_id is None
    assert latest_labels(conn, seeded["source_id"]) == []


def test_record_boundary_correction_flags_only_the_edge_that_moved(conn, seeded):
    record_boundary_correction(
        conn, rally_id="r1", source_id=seeded["source_id"],
        det_start_ms=1000, det_end_ms=5000,
        true_start_ms=1000, true_end_ms=4600,
    )
    row = latest_labels(conn, seeded["source_id"])[0]
    assert parse_flags(row["boundary_flags"]) == ["end_late"]
