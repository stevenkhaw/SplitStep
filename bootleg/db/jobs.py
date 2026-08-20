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
    # BEGIN IMMEDIATE takes the write lock before the SELECT runs. A bare `with
    # conn:` does not: legacy sqlite3 isolation defers BEGIN until the first DML
    # statement, so the SELECT would run in autocommit mode and two connections
    # could both read the same queued row before either UPDATEs it.
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at, id LIMIT 1"
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        # Guard the UPDATE on status='queued' too: defence in depth in case the
        # lock discipline above is ever weakened. If another worker already won,
        # rowcount is 0 and we must not return a job we did not actually claim.
        cur = conn.execute(
            "UPDATE jobs SET status='running', heartbeat_at=?"
            " WHERE id=? AND status='queued'",
            (_now(), row["id"]),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    if cur.rowcount == 0:
        return None
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
