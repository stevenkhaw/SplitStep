import pytest

from bootleg.db.presets import create_preset, get_preset, list_presets
from bootleg.db.rallies import (
    list_rallies,
    replace_rallies,
    set_bounds,
    set_rejected,
    set_star,
)
from bootleg.db.schema import MIGRATIONS, connect, migrate
from bootleg.db.sessions import (
    add_source,
    find_or_create_session_for_date,
    get_source,
    list_sources,
    set_source_preset,
    set_source_rotation,
)
from bootleg.detect.geometry import Quad
from bootleg.detect.segment import Interval

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
    assert migrate(conn) == 5
    assert migrate(conn) == 5


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

    assert migrate(conn) == 5

    row = conn.execute("SELECT * FROM rally_labels").fetchone()
    assert (row["id"], row["verdict"], row["boundary_flags"]) == ("l1", "clean", "end_late")
    # Pre-004 rows are judgements, not retractions.
    assert row["retracted"] == 0
    # The rebuild must not have quietly disabled enforcement for the rest of
    # this connection's life -- 004 deliberately toggles no pragmas.
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


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

    monkeypatch.setattr("bootleg.db.rallies._renumber", boom)

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
    from bootleg.db.sessions import set_source_setup
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
    from bootleg.db.sessions import set_source_setup
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
