import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta


def _now() -> str:
    return datetime.now(UTC).isoformat()


def enqueue(conn: sqlite3.Connection, job_type: str, payload: dict) -> str:
    job_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO jobs (id,type,payload,status,created_at)"
        " VALUES (?,?,?,'queued',?)",
        (job_id, job_type, json.dumps(payload), _now()),
    )
    conn.commit()
    return job_id


def claim(conn: sqlite3.Connection) -> sqlite3.Row | None:
    with conn:  # implicit transaction; SQLite serializes writers
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at, id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE jobs SET status='running', heartbeat_at=? WHERE id=?",
            (_now(), row["id"]),
        )
    return conn.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()


def heartbeat(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute("UPDATE jobs SET heartbeat_at=? WHERE id=?", (_now(), job_id))
    conn.commit()


def set_progress(conn: sqlite3.Connection, job_id: str, progress: float) -> None:
    conn.execute(
        "UPDATE jobs SET progress=?, heartbeat_at=? WHERE id=?",
        (progress, _now(), job_id),
    )
    conn.commit()


def finish(conn: sqlite3.Connection, job_id: str, error: str | None = None) -> None:
    conn.execute(
        "UPDATE jobs SET status=?, error=?, finished_at=? WHERE id=?",
        ("failed" if error else "done", error, _now(), job_id),
    )
    conn.commit()


def reclaim_stale(conn: sqlite3.Connection, older_than_s: int = 120) -> int:
    cutoff = (datetime.now(UTC) - timedelta(seconds=older_than_s)).isoformat()
    cur = conn.execute(
        "UPDATE jobs SET status='queued', heartbeat_at=NULL"
        " WHERE status='running' AND (heartbeat_at IS NULL OR heartbeat_at < ?)",
        (cutoff,),
    )
    conn.commit()
    return cur.rowcount
