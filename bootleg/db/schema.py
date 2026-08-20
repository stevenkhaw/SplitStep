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
    version = _current_version(conn)
    for path in sorted(MIGRATIONS.glob("*.sql")):
        n = int(path.name.split("_", 1)[0])
        if n <= version:
            continue
        conn.executescript(path.read_text())
        conn.execute(f"PRAGMA user_version={n}")
        conn.commit()
        version = n
    return version
