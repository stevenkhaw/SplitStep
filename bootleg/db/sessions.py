import sqlite3
import uuid
from datetime import UTC, datetime


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create_session(conn: sqlite3.Connection, session_id: str,
                   title: str, played_on: str) -> str:
    conn.execute(
        "INSERT INTO sessions (id,title,played_on,status,created_at)"
        " VALUES (?,?,?,'ingesting',?)",
        (session_id, title, played_on, _now()),
    )
    conn.commit()
    return session_id


def find_or_create_session_for_date(conn: sqlite3.Connection, played_on: str) -> str:
    row = conn.execute(
        "SELECT id FROM sessions WHERE played_on = ? ORDER BY id LIMIT 1",
        (played_on,),
    ).fetchone()
    if row:
        return row["id"]
    return create_session(conn, played_on, played_on, played_on)


def add_source(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    recorded_at: str,
    duration_ms: int,
    width: int,
    height: int,
    fps: float,
    original_name: str | None,
) -> tuple[str, int]:
    row = conn.execute(
        "SELECT COALESCE(MAX(idx),0) AS max_idx,"
        " COALESCE(SUM(duration_ms),0) AS total FROM sources WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    idx = row["max_idx"] + 1
    offset_ms = row["total"]
    source_id = uuid.uuid4().hex

    conn.execute(
        "INSERT INTO sources (id,session_id,idx,recorded_at,offset_ms,duration_ms,"
        "width,height,fps,original_name,status) VALUES (?,?,?,?,?,?,?,?,?,?,'ingesting')",
        (source_id, session_id, idx, recorded_at, offset_ms, duration_ms,
         width, height, fps, original_name),
    )
    conn.commit()
    return source_id, idx


def get_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()


def list_sessions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sessions ORDER BY played_on DESC, id DESC"
    ).fetchall()


def list_sources(conn: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sources WHERE session_id = ? ORDER BY idx", (session_id,)
    ).fetchall()


def get_source(conn: sqlite3.Connection, source_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()


def set_source_status(conn: sqlite3.Connection, source_id: str, status: str) -> None:
    conn.execute("UPDATE sources SET status = ? WHERE id = ?", (status, source_id))
    conn.commit()


def set_session_status(conn: sqlite3.Connection, session_id: str, status: str) -> None:
    conn.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))
    conn.commit()
