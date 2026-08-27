import pytest

from splitstep.db.rallies import replace_rallies
from splitstep.db.reels import (
    add_items,
    create_reel,
    delete_reel,
    find_reel_by_name,
    get_reel,
    get_reel_by_slug,
    list_items,
    list_reels,
    mark_dirty,
    mark_rendered,
    remove_item,
    rename_reel,
    set_item_note,
    set_order,
    slugify,
    unique_slug,
)
from splitstep.db.sessions import add_source, find_or_create_session_for_date
from splitstep.detect.segment import Interval


@pytest.fixture
def seeded(conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, _idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    return {"session_id": session_id, "source_id": source_id}


def _spans(conn, reel_id):
    return [(r["source_id"], r["start_ms"], r["end_ms"]) for r in list_items(conn, reel_id)]


def test_reel_items_has_no_rally_foreign_key(conn):
    # The defect this migration exists to fix. 001 declared rally_id with
    # ON DELETE CASCADE; a re-segment would have emptied every reel.
    fks = conn.execute("PRAGMA foreign_key_list(reel_items)").fetchall()
    assert {fk["table"] for fk in fks} == {"reels", "sources"}
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(reel_items)")}
    assert "rally_id" not in cols
    assert {"reel_id", "source_id", "start_ms", "end_ms", "position"} <= cols


def test_a_resegment_leaves_the_reel_intact(conn, seeded):
    # The actual regression test, not just a schema assertion: build a reel
    # from a rally's span, then blow every rally away the way a threshold
    # sweep does, and the reel must still hold its item.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    reel = create_reel(conn, "2026-08-18 points")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 5000)])

    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1200, 4800, 0.7)])

    assert _spans(conn, reel["id"]) == [(seeded["source_id"], 1000, 5000)]


def test_deleting_the_source_does_cascade(conn, seeded):
    # The other half of the asymmetry: no footage, no clip, no item.
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 5000)])
    conn.execute("DELETE FROM sources WHERE id = ?", (seeded["source_id"],))
    conn.commit()
    assert _spans(conn, reel["id"]) == []


def test_slugify_lowercases_and_hyphenates():
    assert slugify("2026-08-18 points") == "2026-08-18-points"
    assert slugify("  Best of  July!  ") == "best-of-july"
    # A name with nothing sluggable still needs a slug, because reels.slug is
    # NOT NULL UNIQUE and the name is the user's to choose.
    assert slugify("!!!") == "reel"


def test_unique_slug_suffixes_on_collision(conn):
    create_reel(conn, "2026-08-18 points")
    assert unique_slug(conn, "2026-08-18-points") == "2026-08-18-points-2"


def test_a_repeated_name_gets_its_own_slug(conn):
    first = create_reel(conn, "2026-08-18 points")
    second = create_reel(conn, "2026-08-18 points")
    assert first["slug"] == "2026-08-18-points"
    assert second["slug"] == "2026-08-18-points-2"
    assert first["id"] != second["id"]


def test_a_new_reel_is_dirty_and_unrendered(conn):
    reel = create_reel(conn, "r")
    assert reel["dirty"] == 1
    assert reel["rendered_path"] is None
    assert reel["rendered_at"] is None


def test_add_items_appends_in_order(conn, seeded):
    reel = create_reel(conn, "r")
    src = seeded["source_id"]
    assert add_items(conn, reel["id"], [(src, 3000, 4000), (src, 1000, 2000)]) == 2
    # Appended in the order given, NOT sorted -- the caller decides order
    # (the session-set route passes rallies in chronological order); once a
    # human reorders, sorting here would silently undo it.
    assert _spans(conn, reel["id"]) == [(src, 3000, 4000), (src, 1000, 2000)]


def test_add_items_is_additive_and_skips_duplicates(conn, seeded):
    reel = create_reel(conn, "r")
    src = seeded["source_id"]
    add_items(conn, reel["id"], [(src, 3000, 4000), (src, 1000, 2000)])
    set_order(conn, reel["id"], [(src, 1000, 2000), (src, 3000, 4000)])

    added = add_items(conn, reel["id"], [(src, 1000, 2000), (src, 5000, 6000)])

    # One genuinely new span appended; the hand-ordering above untouched and
    # nothing removed. Overwriting membership would silently discard a manual
    # reorder -- the same class of mistake replace_rallies makes with
    # boundary edits, which already cost this project a 9.6-second rally.
    assert added == 1
    assert _spans(conn, reel["id"]) == [
        (src, 1000, 2000), (src, 3000, 4000), (src, 5000, 6000),
    ]


def test_add_items_ignores_a_duplicate_within_one_call(conn, seeded):
    reel = create_reel(conn, "r")
    src = seeded["source_id"]
    assert add_items(conn, reel["id"], [(src, 1000, 2000), (src, 1000, 2000)]) == 1


def test_remove_item(conn, seeded):
    reel = create_reel(conn, "r")
    src = seeded["source_id"]
    add_items(conn, reel["id"], [(src, 1000, 2000), (src, 3000, 4000)])
    assert remove_item(conn, reel["id"], src, 1000, 2000) is True
    assert remove_item(conn, reel["id"], src, 1000, 2000) is False
    assert _spans(conn, reel["id"]) == [(src, 3000, 4000)]


def test_remove_item_leaves_positions_ordered(conn, seeded):
    # Positions may gap after a removal; list_items must still be stable and
    # a later add must land at the end, not on top of a surviving row.
    reel = create_reel(conn, "r")
    src = seeded["source_id"]
    add_items(conn, reel["id"], [(src, 1000, 2000), (src, 3000, 4000), (src, 5000, 6000)])
    remove_item(conn, reel["id"], src, 3000, 4000)
    add_items(conn, reel["id"], [(src, 7000, 8000)])
    assert _spans(conn, reel["id"]) == [
        (src, 1000, 2000), (src, 5000, 6000), (src, 7000, 8000),
    ]


def test_set_order_rewrites_positions(conn, seeded):
    reel = create_reel(conn, "r")
    src = seeded["source_id"]
    add_items(conn, reel["id"], [(src, 1000, 2000), (src, 3000, 4000), (src, 5000, 6000)])
    set_order(conn, reel["id"], [(src, 5000, 6000), (src, 1000, 2000), (src, 3000, 4000)])
    assert _spans(conn, reel["id"]) == [
        (src, 5000, 6000), (src, 1000, 2000), (src, 3000, 4000),
    ]


def test_set_order_refuses_a_list_that_is_not_the_membership(conn, seeded):
    # A reorder that adds or drops a span is a bug in the caller, and
    # applying it partially would leave the reel holding an order that
    # describes something other than what it contains.
    reel = create_reel(conn, "r")
    src = seeded["source_id"]
    add_items(conn, reel["id"], [(src, 1000, 2000), (src, 3000, 4000)])
    with pytest.raises(ValueError):
        set_order(conn, reel["id"], [(src, 1000, 2000)])
    with pytest.raises(ValueError):
        set_order(conn, reel["id"], [(src, 1000, 2000), (src, 3000, 4000), (src, 9, 10)])
    assert _spans(conn, reel["id"]) == [(src, 1000, 2000), (src, 3000, 4000)]


def test_mark_rendered_then_dirty(conn):
    reel = create_reel(conn, "r")
    mark_rendered(conn, reel["id"], "reels/r.mp4", set())
    row = get_reel(conn, reel["id"])
    assert row["dirty"] == 0
    assert row["rendered_path"] == "reels/r.mp4"
    assert row["rendered_at"] is not None

    mark_dirty(conn, reel["id"])
    row = get_reel(conn, reel["id"])
    assert row["dirty"] == 1
    # rendered_path survives: the file is still on disk and still playable,
    # it is merely out of date. Clearing it would make "re-render" and
    # "never rendered" indistinguishable in the list.
    assert row["rendered_path"] == "reels/r.mp4"


def test_mark_rendered_clears_dirty_when_membership_still_matches(conn, seeded):
    reel = create_reel(conn, "r")
    src = seeded["source_id"]
    add_items(conn, reel["id"], [(src, 1000, 2000)])

    mark_rendered(conn, reel["id"], "reels/r.mp4", {(src, 1000, 2000)})

    assert get_reel(conn, reel["id"])["dirty"] == 0


def test_mark_rendered_leaves_dirty_set_if_membership_changed_mid_render(conn, seeded):
    """The window Finding 4 exists for: handle_reel resolves items, then
    spends up to minutes concatenating them. An add_items landing in that
    window sets dirty = 1 for a reason still true when mark_rendered runs --
    the file on disk does not contain what the reel now holds -- and that
    must survive the call, not be clobbered by it.
    """
    reel = create_reel(conn, "r")
    src = seeded["source_id"]
    add_items(conn, reel["id"], [(src, 1000, 2000)])
    rendered_membership = {(src, 1000, 2000)}

    # The concurrent edit: lands after the render's membership was captured,
    # before mark_rendered is called.
    add_items(conn, reel["id"], [(src, 3000, 4000)])

    mark_rendered(conn, reel["id"], "reels/r.mp4", rendered_membership)

    row = get_reel(conn, reel["id"])
    assert row["dirty"] == 1
    # The render itself still succeeded and is still recorded -- only the
    # dirty bit records that it is already stale.
    assert row["rendered_path"] == "reels/r.mp4"
    assert row["rendered_at"] is not None


def test_list_reels_carries_an_item_count(conn, seeded):
    create_reel(conn, "empty")
    full = create_reel(conn, "full")
    add_items(conn, full["id"], [(seeded["source_id"], 1000, 2000)])
    by_slug = {r["slug"]: r["item_count"] for r in list_reels(conn)}
    assert by_slug == {"empty": 0, "full": 1}


def test_lookup_helpers(conn):
    reel = create_reel(conn, "2026-08-18 points")
    assert get_reel_by_slug(conn, "2026-08-18-points")["id"] == reel["id"]
    assert get_reel_by_slug(conn, "nope") is None
    assert find_reel_by_name(conn, "2026-08-18 points")["id"] == reel["id"]
    assert find_reel_by_name(conn, "nope") is None


def test_rename_reel_updates_the_name_only(conn):
    reel = create_reel(conn, "old name")
    mark_rendered(conn, reel["id"], "reels/old-name.mp4", set())
    conn.execute("UPDATE reels SET dirty = 0 WHERE id = ?", (reel["id"],))
    conn.commit()
    before = get_reel(conn, reel["id"])

    rename_reel(conn, reel["id"], "new name")

    after = get_reel(conn, reel["id"])
    assert after["name"] == "new name"
    # The slug, dirty, rendered_path and rendered_at are untouched -- see
    # rename_reel's docstring for why each one matters.
    assert after["slug"] == before["slug"]
    assert after["dirty"] == before["dirty"]
    assert after["rendered_path"] == before["rendered_path"]
    assert after["rendered_at"] == before["rendered_at"]


def test_delete_reel_removes_the_row_and_cascades_its_items(conn, seeded):
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 2000)])

    delete_reel(conn, reel["id"])

    assert get_reel(conn, reel["id"]) is None
    assert conn.execute(
        "SELECT COUNT(*) c FROM reel_items WHERE reel_id = ?", (reel["id"],)
    ).fetchone()["c"] == 0


def test_set_item_note_round_trips(conn, seeded):
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 2000)])
    assert set_item_note(conn, reel["id"], seeded["source_id"], 1000, 2000, "match point") is True
    row = list_items(conn, reel["id"])[0]
    assert row["note"] == "match point"


def test_set_item_note_on_a_missing_item_is_false(conn, seeded):
    reel = create_reel(conn, "r")
    assert set_item_note(conn, reel["id"], seeded["source_id"], 1, 2, "x") is False


def test_set_item_note_marks_the_reel_dirty(conn, seeded):
    # A numbered render burns the note into the file, so a note edit after a
    # render means the file no longer shows what the reel says -- the same
    # staleness signal a membership change raises.
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 2000)])
    mark_rendered(conn, reel["id"], "reels/r.mp4",
                  {(seeded["source_id"], 1000, 2000)})
    assert get_reel(conn, reel["id"])["dirty"] == 0
    set_item_note(conn, reel["id"], seeded["source_id"], 1000, 2000, "match point")
    assert get_reel(conn, reel["id"])["dirty"] == 1


def test_a_missed_note_write_does_not_mark_dirty(conn, seeded):
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 2000)])
    mark_rendered(conn, reel["id"], "reels/r.mp4",
                  {(seeded["source_id"], 1000, 2000)})
    set_item_note(conn, reel["id"], seeded["source_id"], 1, 2, "x")
    assert get_reel(conn, reel["id"])["dirty"] == 0
