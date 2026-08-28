import sqlite3
from pathlib import Path

MIGRATIONS = Path(__file__).parent / "migrations"


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> int:
    """Apply every migration file numbered above the database's current version.

    Numbers are collected and checked for a collision BEFORE any file runs.
    Two files sharing a leading number sort however the filesystem returns
    them; whichever sorts second would be silently SKIPPED here forever, on
    every database that has already reached that version -- no exception,
    and no sign in this function's own return value that anything was
    missed, because the skipped file's number is never reached as "new"
    again. This project has lost that round twice: most recently
    006_reel_items_by_span silently lost to 006_rally_notes on this very
    branch, caught only because someone happened to run pytest before
    merging. git cannot see the collision either -- the filenames differ, so
    a merge that combines two parallel branches' migrations is clean by its
    lights. test_no_two_migrations_share_a_number in tests/test_db.py catches
    this too, but only for whoever runs the suite; this is the same check
    run here, against every real invocation, so a library that already has
    an old MIGRATIONS directory cached (or a caller that forgets to test)
    cannot get partway through a merge with the collision live.
    """
    paths = sorted(MIGRATIONS.glob("*.sql"))
    numbers = [int(path.name.split("_", 1)[0]) for path in paths]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    if duplicates:
        raise RuntimeError(
            f"migrations share these leading number(s): {duplicates} -- "
            "rename one before migrating anything"
        )

    version = _current_version(conn)
    for path, n in zip(paths, numbers, strict=True):
        if n <= version:
            continue
        conn.executescript(path.read_text())
        conn.execute(f"PRAGMA user_version={n}")
        conn.commit()
        version = n
    return version
