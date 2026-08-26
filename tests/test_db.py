import sqlite3

import pytest

from splitstep.db.presets import create_preset, get_preset, list_presets
from splitstep.db.rallies import (
    list_rallies,
    replace_rallies,
    set_bounds,
    set_clip_path,
    set_rejected,
    set_star,
)
from splitstep.db.schema import MIGRATIONS, connect, migrate
from splitstep.db.sessions import (
    add_source,
    find_or_create_session_for_date,
    get_source,
    list_sources,
    set_source_preset,
    set_source_rotation,
)
from splitstep.detect.geometry import Quad
from splitstep.detect.segment import Interval

SAMPLE_QUAD = Quad(((0.1, 0.9), (0.9, 0.9), (0.7, 0.3), (0.3, 0.3)))


def test_connect_sets_wal_and_full_sync(library):
    conn = connect(library.db_path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrate_creates_all_tables(library):
    conn = connect(library.db_path)
    migrate(conn)
    names = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "sessions", "sources", "rallies", "court_presets",
        "reels", "reel_items", "jobs", "rally_labels",
    } <= names


def test_migrate_is_idempotent(library):
    conn = connect(library.db_path)
    assert migrate(conn) == 10
    assert migrate(conn) == 10


def test_no_two_migrations_share_a_number():
    """Two migrations numbered the same silently lose one of them, forever.

    migrate() derives each file's version from its leading integer and skips
    anything numbered <= the version already reached. So of two files sharing
    a number, sorted order applies the first, sets user_version to it, and the
    second is skipped on that database and every database after it -- with no
    error, and with migrate() still returning the number the caller expected.

    This project has now lost a round to that twice: 006_rally_notes and
    006_reel_items_by_span were written on parallel branches and merged
    cleanly, because the filenames differ and git has no idea the numbers
    mean anything. Asserting on a specific column would only catch the
    collision we already know about; this catches the next one, at the moment
    someone adds the file rather than months later when a table is quietly
    the wrong shape.
    """
    numbers = [int(path.name.split("_", 1)[0]) for path in MIGRATIONS.glob("*.sql")]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    assert not duplicates, f"migrations share these numbers: {duplicates}"


def test_migrate_refuses_before_applying_anything_if_two_files_share_a_number(
    tmp_path, monkeypatch
):
    """The structural guard, not just the authoring-time assertion above.
    test_no_two_migrations_share_a_number only protects a run that happens
    to include it; this is the same check run inside migrate() itself, so a
    real database cannot get partway migrated with the collision still live
    -- 001 (no collision at all) must NOT have applied either.
    """
    import splitstep.db.schema as schema_mod

    fake_migrations = tmp_path / "migrations"
    fake_migrations.mkdir()
    (fake_migrations / "001_first.sql").write_text("CREATE TABLE a (id INTEGER);")
    (fake_migrations / "002_second.sql").write_text("CREATE TABLE b (id INTEGER);")
    (fake_migrations / "002_second_too.sql").write_text("CREATE TABLE c (id INTEGER);")
    monkeypatch.setattr(schema_mod, "MIGRATIONS", fake_migrations)

    conn = connect(tmp_path / "collision.db")
    with pytest.raises(RuntimeError, match=r"\[2\]"):
        schema_mod.migrate(conn)

    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "a" not in tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0


def test_every_migration_applies_to_a_fresh_database(tmp_path):
    """A fresh database really carries the effect of EVERY migration.

    The version number alone proves only that the highest-numbered file ran.
    When 006_reel_items_by_span collided with 006_rally_notes, migrate()
    returned 6 and rallies.note existed -- and reel_items was still the
    pre-006 shape, because the second 006 had been skipped. Nothing failed.

    So this asserts the youngest observable effect of each recent migration
    rather than the version: 005's point column, 006's note column, and 007
    having actually replaced reel_items (no rally_id, and a source_id foreign
    key instead).
    """
    conn = connect(tmp_path / "fresh.db")
    migrate(conn)

    rallies = {r["name"] for r in conn.execute("PRAGMA table_info(rallies)")}
    assert "point" in rallies       # 005
    assert "note" in rallies        # 006

    reel_items = {r["name"] for r in conn.execute("PRAGMA table_info(reel_items)")}
    assert "rally_id" not in reel_items                          # 007 replaced it
    assert {"source_id", "start_ms", "end_ms"} <= reel_items
    targets = {fk["table"] for fk in conn.execute("PRAGMA foreign_key_list(reel_items)")}
    assert targets == {"reels", "sources"}


def test_migration_004_rebuilds_rally_labels_without_losing_rows(tmp_path):
    # 004 adds `retracted` and relaxes a CHECK, which sqlite can only do by
    # rebuilding the table -- create, copy, drop, rename. A rebuild that
    # forgot the copy would take an already-collected corpus with it, and
    # nothing else in the app would notice until the next export came back
    # empty. Migrate to 003, plant a row, then let 004 run over it.
    # A raw database, not the `library` fixture -- that one is already fully
    # migrated, and this test needs to stand at 003 with data in it.
    conn = connect(tmp_path / "old.db")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        n = int(path.name.split("_", 1)[0])
        if n > 3:
            break
        conn.executescript(path.read_text())
        conn.execute(f"PRAGMA user_version={n}")
    conn.execute(
        "INSERT INTO sessions (id,title,played_on,status,created_at)"
        " VALUES ('s1','t','2026-08-19','ready','now')"
    )
    conn.execute(
        "INSERT INTO sources (id,session_id,idx,recorded_at,offset_ms,duration_ms,"
        "width,height,fps,rotation_deg,original_name,status)"
        " VALUES ('src1','s1',1,'now',0,1000,1920,1080,30.0,0,'a.mov','ready')"
    )
    conn.execute(
        "INSERT INTO rally_labels (id,source_id,span_start_ms,span_end_ms,verdict,"
        "boundary_flags,labelled_at) VALUES ('l1','src1',1000,5000,'clean','end_late','T')"
    )
    conn.commit()

    assert migrate(conn) == 10

    row = conn.execute("SELECT * FROM rally_labels").fetchone()
    assert (row["id"], row["verdict"], row["boundary_flags"]) == ("l1", "clean", "end_late")
    # Pre-004 rows are judgements, not retractions.
    assert row["retracted"] == 0
    # The rebuild must not have quietly disabled enforcement for the rest of
    # this connection's life -- 004 deliberately toggles no pragmas.
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migration_005_backfills_point_from_star_and_clears_star(tmp_path):
    # 005 is the one irreversible reinterpretation of a user's real data on
    # this branch: every existing star meant "a point was played out", so it
    # copies star to the new `point` column and then clears every star,
    # leaving `starred` free to mean "a highlight" going forward. It runs
    # exactly once, ever, against real libraries -- there is no test using a
    # raw pre-005 database today; every test in test_rallies_point.py starts
    # from the already-migrated `conn` fixture, so this backfill has run
    # against nothing but the (untested) assumption that it is correct.
    # Migrate to 004, plant one rally per combination the backfill has to
    # get right, then let 005 run over them.
    conn = connect(tmp_path / "old.db")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        n = int(path.name.split("_", 1)[0])
        if n > 4:
            break
        conn.executescript(path.read_text())
        conn.execute(f"PRAGMA user_version={n}")
    conn.execute(
        "INSERT INTO sessions (id,title,played_on,status,created_at)"
        " VALUES ('s1','t','2026-08-19','ready','now')"
    )
    conn.execute(
        "INSERT INTO sources (id,session_id,idx,recorded_at,offset_ms,duration_ms,"
        "width,height,fps,rotation_deg,original_name,status)"
        " VALUES ('src1','s1',1,'now',0,1000,1920,1080,30.0,0,'a.mov','ready')"
    )
    # Starred, not rejected -- the ordinary case: a filmed tiebreaker where
    # star was the only mark available for "this is a point".
    conn.execute(
        "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
        "det_start_ms,det_end_ms,confidence,starred,rejected)"
        " VALUES ('r_star','s1','src1',1,0,1000,0,1000,0.9,1,0)"
    )
    # Rejected, not starred -- a bad detection. rejected must survive
    # untouched; point must not be invented for it.
    conn.execute(
        "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
        "det_start_ms,det_end_ms,confidence,starred,rejected)"
        " VALUES ('r_rejected','s1','src1',2,1000,2000,1000,2000,0.9,0,1)"
    )
    # Neither -- an ordinary unreviewed rally, must come out with point = 0.
    conn.execute(
        "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
        "det_start_ms,det_end_ms,confidence,starred,rejected)"
        " VALUES ('r_plain','s1','src1',3,2000,3000,2000,3000,0.9,0,0)"
    )
    conn.commit()

    assert migrate(conn) == 10

    rows = {r["id"]: r for r in conn.execute("SELECT * FROM rallies").fetchall()}
    # point equals the old starred, per row.
    assert rows["r_star"]["point"] == 1
    assert rows["r_rejected"]["point"] == 0
    assert rows["r_plain"]["point"] == 0
    # every starred is 0 afterwards, including the row that used to be 1.
    assert rows["r_star"]["starred"] == 0
    assert rows["r_rejected"]["starred"] == 0
    assert rows["r_plain"]["starred"] == 0
    # rejected is untouched by a migration that only ever reads/writes star
    # and point.
    assert rows["r_rejected"]["rejected"] == 1
    assert rows["r_star"]["rejected"] == 0
    assert rows["r_plain"]["rejected"] == 0


def test_migration_008_backfills_seen_at_from_reviewed_at(tmp_path):
    # 008 splits "seen" from "reviewed" (see rally_seen tests for the
    # runtime behavior). Every row that already carries a reviewed_at was
    # necessarily seen at exactly that moment -- a ruling cannot be made on
    # a rally nobody looked at -- so seen_at backfills from it. Without this,
    # every existing library's very next queue open would regress to rally
    # 1: a real, if one-time, replay of the bug this migration exists to
    # fix. Migrate to 007, plant one reviewed row and one never-touched row,
    # then let 008 run over them.
    conn = connect(tmp_path / "old.db")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        n = int(path.name.split("_", 1)[0])
        if n > 7:
            break
        conn.executescript(path.read_text())
        conn.execute(f"PRAGMA user_version={n}")
    conn.execute(
        "INSERT INTO sessions (id,title,played_on,status,created_at)"
        " VALUES ('s1','t','2026-08-19','ready','now')"
    )
    conn.execute(
        "INSERT INTO sources (id,session_id,idx,recorded_at,offset_ms,duration_ms,"
        "width,height,fps,rotation_deg,original_name,status)"
        " VALUES ('src1','s1',1,'now',0,1000,1920,1080,30.0,0,'a.mov','ready')"
    )
    # Already ruled on -- must gain seen_at equal to its own reviewed_at.
    conn.execute(
        "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
        "det_start_ms,det_end_ms,confidence,starred,rejected,reviewed_at)"
        " VALUES ('r_reviewed','s1','src1',1,0,1000,0,1000,0.9,1,0,'2026-08-19T10:00:00+00:00')"
    )
    # Never touched -- must come out with seen_at still NULL, not
    # invented from nothing.
    conn.execute(
        "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
        "det_start_ms,det_end_ms,confidence,starred,rejected)"
        " VALUES ('r_untouched','s1','src1',2,1000,2000,1000,2000,0.9,0,0)"
    )
    conn.commit()

    assert migrate(conn) == 10

    rows = {r["id"]: r for r in conn.execute("SELECT * FROM rallies").fetchall()}
    assert rows["r_reviewed"]["seen_at"] == rows["r_reviewed"]["reviewed_at"]
    assert rows["r_reviewed"]["seen_at"] == "2026-08-19T10:00:00+00:00"
    assert rows["r_untouched"]["seen_at"] is None
    assert rows["r_untouched"]["reviewed_at"] is None


def test_rally_cascades_when_source_deleted(library):
    conn = connect(library.db_path)
    migrate(conn)
    conn.execute(
        "INSERT INTO sessions (id,title,played_on,status,created_at)"
        " VALUES ('s1','t','2026-08-19','ready','now')"
    )
    conn.execute(
        "INSERT INTO sources (id,session_id,idx,recorded_at,offset_ms,duration_ms,"
        "width,height,fps,status) VALUES ('src1','s1',1,'now',0,1000,1920,1080,30,'ready')"
    )
    conn.execute(
        "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
        "det_start_ms,det_end_ms,confidence) VALUES ('r1','s1','src1',1,0,100,0,100,0.9)"
    )
    conn.commit()
    conn.execute("DELETE FROM sources WHERE id='src1'")
    conn.commit()
    assert conn.execute("SELECT count(*) FROM rallies").fetchone()[0] == 0


@pytest.fixture
def conn(library):
    c = connect(library.db_path)
    migrate(c)
    return c


def _add(c, session_id, duration_ms):
    return add_source(c, session_id, recorded_at="2026-08-19T10:00:00Z",
                      duration_ms=duration_ms, width=3840, height=2160,
                      fps=30.0, original_name="IMG_0001.MOV")


def test_find_or_create_is_stable_for_the_same_date(conn):
    a = find_or_create_session_for_date(conn, "2026-08-19")
    b = find_or_create_session_for_date(conn, "2026-08-19")
    assert a == b


def test_sources_get_sequential_idx_and_cumulative_offset(conn):
    s = find_or_create_session_for_date(conn, "2026-08-19")
    _, idx1 = _add(conn, s, 60_000)
    _, idx2 = _add(conn, s, 30_000)
    assert (idx1, idx2) == (1, 2)
    rows = list_sources(conn, s)
    assert [r["offset_ms"] for r in rows] == [0, 60_000]


def test_replace_rallies_writes_det_columns(conn):
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src, _ = _add(conn, s, 60_000)
    replace_rallies(conn, s, src, [Interval(1000, 4000, 0.8)])
    r = list_rallies(conn, s)[0]
    assert (r["start_ms"], r["end_ms"]) == (1000, 4000)
    assert (r["det_start_ms"], r["det_end_ms"]) == (1000, 4000)


def test_set_bounds_leaves_det_columns_untouched(conn):
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src, _ = _add(conn, s, 60_000)
    replace_rallies(conn, s, src, [Interval(1000, 4000, 0.8)])
    rally_id = list_rallies(conn, s)[0]["id"]
    set_bounds(conn, rally_id, 1200, 3800)
    r = list_rallies(conn, s)[0]
    assert (r["start_ms"], r["end_ms"]) == (1200, 3800)
    assert (r["det_start_ms"], r["det_end_ms"]) == (1000, 4000)


def test_replace_rallies_preserves_stars_by_overlap(conn):
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src, _ = _add(conn, s, 60_000)
    replace_rallies(conn, s, src, [Interval(1000, 5000, 0.8)])
    set_star(conn, list_rallies(conn, s)[0]["id"], True)

    # re-segment produces a slightly different but heavily overlapping segment
    replace_rallies(conn, s, src, [Interval(1200, 5200, 0.7)])
    rows = list_rallies(conn, s)
    assert len(rows) == 1
    assert rows[0]["starred"] == 1


def test_replace_rallies_drops_stars_when_overlap_is_small(conn):
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src, _ = _add(conn, s, 60_000)
    replace_rallies(conn, s, src, [Interval(1000, 5000, 0.8)])
    set_star(conn, list_rallies(conn, s)[0]["id"], True)

    replace_rallies(conn, s, src, [Interval(30_000, 34_000, 0.7)])
    assert list_rallies(conn, s)[0]["starred"] == 0


def test_rallies_are_renumbered_across_sources(conn):
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src1, _ = _add(conn, s, 60_000)
    src2, _ = _add(conn, s, 60_000)
    replace_rallies(conn, s, src1, [Interval(1000, 4000, 0.8)])
    replace_rallies(conn, s, src2, [Interval(2000, 5000, 0.8)])
    assert [r["idx"] for r in list_rallies(conn, s)] == [1, 2]


def test_renumber_is_collision_free_when_a_non_last_source_grows(conn):
    # test_rallies_are_renumbered_across_sources gives each source exactly
    # one rally -- the single arrangement where a straight 1..N renumbering
    # pass can never walk into a sibling's still-live idx, because there is
    # never more than one row ahead of any other. Here both sources start
    # with three rallies each (idx 1-3, 4-6) and source 1 -- not the last
    # source -- grows to five. Renumbering source 1's rows in place now has
    # to pass through idx values 4 and beyond while source 2's rows still
    # sit on their old idx 4-6, which is exactly what collides under
    # UNIQUE(session_id, idx) unless _renumber offsets every row in the
    # session first.
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src1, _ = _add(conn, s, 60_000)
    src2, _ = _add(conn, s, 60_000)
    three = [Interval(1000, 2000, 0.8), Interval(3000, 4000, 0.8), Interval(5000, 6000, 0.8)]
    replace_rallies(conn, s, src1, three)
    replace_rallies(conn, s, src2, three)

    five = [
        Interval(1000, 2000, 0.8), Interval(3000, 4000, 0.8), Interval(5000, 6000, 0.8),
        Interval(7000, 8000, 0.8), Interval(9000, 10000, 0.8),
    ]
    replace_rallies(conn, s, src1, five)

    idxs = [r["idx"] for r in list_rallies(conn, s)]
    assert idxs == list(range(1, 9))
    assert len(idxs) == len(set(idxs))
    assert all(i > 0 for i in idxs)


def test_replace_rallies_rolls_back_cleanly_on_failure(conn, monkeypatch):
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src, _ = _add(conn, s, 60_000)
    replace_rallies(conn, s, src, [Interval(1000, 4000, 0.8)])
    rally_id = list_rallies(conn, s)[0]["id"]
    set_star(conn, rally_id, True)

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("splitstep.db.rallies._renumber", boom)

    with pytest.raises(RuntimeError):
        replace_rallies(conn, s, src, [Interval(9000, 12000, 0.9)])

    rows = list_rallies(conn, s)
    assert len(rows) == 1
    assert rows[0]["id"] == rally_id
    assert rows[0]["starred"] == 1
    assert (rows[0]["start_ms"], rows[0]["end_ms"]) == (1000, 4000)


def test_replace_rallies_preserves_rejected_by_overlap(conn):
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src, _ = _add(conn, s, 60_000)
    replace_rallies(conn, s, src, [Interval(1000, 5000, 0.8)])
    set_rejected(conn, list_rallies(conn, s)[0]["id"], True)

    # re-segment produces a slightly different but heavily overlapping segment
    replace_rallies(conn, s, src, [Interval(1200, 5200, 0.7)])
    rows = list_rallies(conn, s)
    assert len(rows) == 1
    assert rows[0]["rejected"] == 1


def test_replace_rallies_drops_rejected_when_overlap_is_small(conn):
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src, _ = _add(conn, s, 60_000)
    replace_rallies(conn, s, src, [Interval(1000, 5000, 0.8)])
    set_rejected(conn, list_rallies(conn, s)[0]["id"], True)

    replace_rallies(conn, s, src, [Interval(30_000, 34_000, 0.7)])
    assert list_rallies(conn, s)[0]["rejected"] == 0


def test_replace_rallies_carries_clip_path_when_the_span_is_unchanged(conn):
    """set_clip_path's docstring: the column records "what WAS cut". Before
    this fix, the INSERT in replace_rallies never listed clip_path at all,
    so any re-segment -- even one that left every span untouched -- wiped
    every recorded clip path in the session to NULL.

    Carrying it forward is also what makes `clips prune` clear it: a claim
    that propagates across sweeps is one that outlives the file it names
    unless something nulls it out.
    """
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src, _ = _add(conn, s, 60_000)
    replace_rallies(conn, s, src, [Interval(1000, 5000, 0.8)])
    rally_id = list_rallies(conn, s)[0]["id"]
    set_clip_path(conn, rally_id, "sessions/2026-08-19/clips/01-1000-5000.mp4")

    # A re-segment that reproduces this rally's exact span unchanged,
    # alongside a genuinely new one it must not invent a path for.
    replace_rallies(conn, s, src, [Interval(1000, 5000, 0.8), Interval(9000, 12000, 0.7)])

    rows = {(r["start_ms"], r["end_ms"]): r for r in list_rallies(conn, s)}
    assert rows[(1000, 5000)]["clip_path"] == "sessions/2026-08-19/clips/01-1000-5000.mp4"
    assert rows[(9000, 12000)]["clip_path"] is None


def test_replace_rallies_drops_clip_path_when_bounds_shift_even_slightly(conn):
    """clip_path is span-specific, unlike starred/rejected/point, which is
    why it needs its own carry-over rule rather than reusing _overlaps_any.
    A boundary nudge small enough to keep the star by >50% overlap must NOT
    keep pointing at a clip cut for the old span: that file's span no longer
    matches the rally's new bounds, so clip_relpath of the current bounds
    resolves to a different, nonexistent path -- exactly the "exists or does
    not" property clip_relpath's docstring describes.
    """
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src, _ = _add(conn, s, 60_000)
    replace_rallies(conn, s, src, [Interval(1000, 5000, 0.8)])
    rally_id = list_rallies(conn, s)[0]["id"]
    set_clip_path(conn, rally_id, "sessions/2026-08-19/clips/01-1000-5000.mp4")
    set_star(conn, rally_id, True)

    # Shifted just enough to still count as the same rally for the star
    # carry-over (>50% overlap), but not the identical span.
    replace_rallies(conn, s, src, [Interval(1200, 5200, 0.7)])

    row = list_rallies(conn, s)[0]
    assert row["starred"] == 1
    assert row["clip_path"] is None


def test_set_rejected_hides_nothing_but_flags_the_row(conn):
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src, _ = _add(conn, s, 60_000)
    replace_rallies(conn, s, src, [Interval(1000, 4000, 0.8)])
    rally_id = list_rallies(conn, s)[0]["id"]
    set_rejected(conn, rally_id, True)
    assert list_rallies(conn, s)[0]["rejected"] == 1


# -- court presets ------------------------------------------------------------

def test_create_preset_round_trips_through_the_database(conn):
    preset_id = create_preset(conn, "backyard", SAMPLE_QUAD)
    row = get_preset(conn, preset_id)
    assert row["name"] == "backyard"
    assert Quad.from_json(row["quad"]) == SAMPLE_QUAD


def test_list_presets_returns_every_preset(conn):
    create_preset(conn, "backyard", SAMPLE_QUAD)
    create_preset(conn, "park court 3", SAMPLE_QUAD)
    names = {r["name"] for r in list_presets(conn)}
    assert names == {"backyard", "park court 3"}


def test_get_preset_returns_none_for_an_unknown_id(conn):
    assert get_preset(conn, "no-such-preset") is None


def test_set_source_preset_assigns_the_preset_to_the_source(conn):
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src, _ = _add(conn, s, 60_000)
    preset_id = create_preset(conn, "backyard", SAMPLE_QUAD)

    set_source_preset(conn, src, preset_id)

    row = conn.execute("SELECT court_preset_id FROM sources WHERE id = ?", (src,)).fetchone()
    assert row["court_preset_id"] == preset_id


# -- rotation -----------------------------------------------------------------


def test_migration_adds_rotation_defaulting_to_zero(tmp_path):
    conn = connect(tmp_path / "l.db")
    migrate(conn)
    session_id = find_or_create_session_for_date(conn, "2026-08-20")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-20", duration_ms=1000,
        width=1920, height=1080, fps=30.0, original_name="a.mov",
    )
    assert get_source(conn, source_id)["rotation_deg"] == 0


def test_add_source_stores_an_explicit_rotation(tmp_path):
    conn = connect(tmp_path / "l.db")
    migrate(conn)
    session_id = find_or_create_session_for_date(conn, "2026-08-20")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-20", duration_ms=1000,
        width=2160, height=3840, fps=30.0, original_name="a.mov", rotation_deg=90,
    )
    assert get_source(conn, source_id)["rotation_deg"] == 90


def test_set_source_rotation_updates_in_place(tmp_path):
    conn = connect(tmp_path / "l.db")
    migrate(conn)
    session_id = find_or_create_session_for_date(conn, "2026-08-20")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-20", duration_ms=1000,
        width=1920, height=1080, fps=30.0, original_name="a.mov",
    )
    set_source_rotation(conn, source_id, 270)
    assert get_source(conn, source_id)["rotation_deg"] == 270


def test_set_source_rotation_rejects_a_non_right_angle(tmp_path):
    conn = connect(tmp_path / "l.db")
    migrate(conn)
    session_id = find_or_create_session_for_date(conn, "2026-08-20")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-20", duration_ms=1000,
        width=1920, height=1080, fps=30.0, original_name="a.mov",
    )
    with pytest.raises(ValueError, match="0, 90, 180 or 270"):
        set_source_rotation(conn, source_id, 45)


def test_set_source_setup_writes_both_columns(tmp_path):
    from splitstep.db.sessions import set_source_setup
    conn = connect(tmp_path / "l.db")
    migrate(conn)
    session_id = find_or_create_session_for_date(conn, "2026-08-20")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-20", duration_ms=1000,
        width=1920, height=1080, fps=30.0, original_name="a.mov",
    )
    preset_id = create_preset(conn, "court", SAMPLE_QUAD)

    set_source_setup(conn, source_id, 180, preset_id)

    row = get_source(conn, source_id)
    assert row["rotation_deg"] == 180
    assert row["court_preset_id"] == preset_id


def test_set_source_setup_rejects_illegal_rotation_without_writing(tmp_path):
    from splitstep.db.sessions import set_source_setup
    conn = connect(tmp_path / "l.db")
    migrate(conn)
    session_id = find_or_create_session_for_date(conn, "2026-08-20")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-20", duration_ms=1000,
        width=1920, height=1080, fps=30.0, original_name="a.mov",
    )
    preset_id = create_preset(conn, "court", SAMPLE_QUAD)
    original_row = get_source(conn, source_id)

    with pytest.raises(ValueError, match="0, 90, 180 or 270"):
        set_source_setup(conn, source_id, 45, preset_id)

    row = get_source(conn, source_id)
    assert row["rotation_deg"] == original_row["rotation_deg"]
    assert row["court_preset_id"] == original_row["court_preset_id"]


def test_migration_009_rebuilds_rallies_without_losing_rows(tmp_path):
    # 009 makes det_start_ms/det_end_ms nullable, which sqlite can only do by
    # rebuilding the table -- create, copy, drop, rename. A rebuild that
    # forgot the copy would take an entire session's review work with it
    # (stars, points, notes, seen_at) and nothing would notice until the
    # queue reopened empty. Migrate to 008, plant a fully-populated rally,
    # then let 009 run over it.
    conn = connect(tmp_path / "old.db")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        n = int(path.name.split("_", 1)[0])
        if n > 8:
            break
        conn.executescript(path.read_text())
        conn.execute(f"PRAGMA user_version={n}")
    conn.execute(
        "INSERT INTO sessions (id,title,played_on,status,created_at)"
        " VALUES ('s1','t','2026-08-19','ready','now')"
    )
    conn.execute(
        "INSERT INTO sources (id,session_id,idx,recorded_at,offset_ms,duration_ms,"
        "width,height,fps,rotation_deg,original_name,status)"
        " VALUES ('src1','s1',1,'now',0,1000,1920,1080,30.0,0,'a.mov','ready')"
    )
    conn.execute(
        "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
        "det_start_ms,det_end_ms,confidence,starred,rejected,point,reviewed_at,"
        "clip_path,note,seen_at) VALUES ('r1','s1','src1',1,1100,5200,1000,5000,"
        "0.83,1,0,1,'T1','clips/a.mp4','late backhand','T0')"
    )
    conn.commit()

    assert migrate(conn) == 10

    row = conn.execute("SELECT * FROM rallies").fetchone()
    # Every column, not just the two being altered: the whole risk of a
    # rebuild is a column dropped from the INSERT ... SELECT copy.
    assert (row["id"], row["session_id"], row["source_id"], row["idx"]) == (
        "r1", "s1", "src1", 1)
    assert (row["start_ms"], row["end_ms"]) == (1100, 5200)
    assert (row["det_start_ms"], row["det_end_ms"]) == (1000, 5000)
    assert row["confidence"] == 0.83
    assert (row["starred"], row["rejected"], row["point"]) == (1, 0, 1)
    assert (row["reviewed_at"], row["seen_at"]) == ("T1", "T0")
    assert (row["clip_path"], row["note"]) == ("clips/a.mp4", "late backhand")
    # The rebuild must not have quietly disabled enforcement for the rest of
    # this connection's life -- 009 toggles no pragmas, same as 004.
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migration_009_allows_a_null_det_span(conn):
    # The point of the migration: a rally a human made, which the detector
    # never proposed. NULL det is the marker -- see the spec's section 3.
    conn.execute(
        "INSERT INTO sessions (id,title,played_on,status,created_at)"
        " VALUES ('s2','t','2026-08-19','ready','now')"
    )
    conn.execute(
        "INSERT INTO sources (id,session_id,idx,recorded_at,offset_ms,duration_ms,"
        "width,height,fps,rotation_deg,original_name,status)"
        " VALUES ('src2','s2',1,'now',0,1000,1920,1080,30.0,0,'b.mov','ready')"
    )
    conn.execute(
        "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
        "det_start_ms,det_end_ms,confidence) VALUES"
        " ('r2','s2','src2',1,1000,5000,NULL,NULL,0.5)"
    )
    conn.commit()
    row = conn.execute("SELECT * FROM rallies WHERE id='r2'").fetchone()
    assert row["det_start_ms"] is None
    assert row["det_end_ms"] is None


def test_migration_009_rejects_a_half_present_det_span(conn):
    # Both or neither. A half-present span is a third state nothing knows how
    # to read: every consumer asks `det_start_ms IS NULL` and that question
    # must answer for the pair. Before 009, the same insert raised IntegrityError
    # from the old NOT NULL constraint, so the bare raises() passed for the wrong
    # reason; match= verifies the CHECK is what now fires.
    conn.execute(
        "INSERT INTO sessions (id,title,played_on,status,created_at)"
        " VALUES ('s3','t','2026-08-19','ready','now')"
    )
    conn.execute(
        "INSERT INTO sources (id,session_id,idx,recorded_at,offset_ms,duration_ms,"
        "width,height,fps,rotation_deg,original_name,status)"
        " VALUES ('src3','s3',1,'now',0,1000,1920,1080,30.0,0,'c.mov','ready')"
    )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute(
            "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
            "det_start_ms,det_end_ms,confidence) VALUES"
            " ('r3','s3','src3',1,1000,5000,1000,NULL,0.5)"
        )
