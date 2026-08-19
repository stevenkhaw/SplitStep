from bootleg.db.schema import connect, migrate


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
        "reels", "reel_items", "jobs",
    } <= names


def test_migrate_is_idempotent(library):
    conn = connect(library.db_path)
    assert migrate(conn) == 1
    assert migrate(conn) == 1


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
