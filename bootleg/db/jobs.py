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


def has_pending_job(conn: sqlite3.Connection, job_type: str, source_id: str) -> bool:
    """True if a job_type job for source_id is already 'queued' or
    'running'.

    Guards against a duplicate enqueue: reclaim_stale() can requeue a
    handler that already ran to completion and enqueued its own follow-up
    job (e.g. build_proxy enqueuing detect) if the worker dies after that
    enqueue but before the original job's row is written 'done'. A second
    copy of the follow-up job would then redo work that silently discards
    state a human may have changed since the first copy ran (see
    handle_detect / replace_rallies). Matches via json_extract on the
    decoded payload, not the raw payload text, so a source_id that is a
    prefix of another's (e.g. 'src-1' vs 'src-10') cannot false-match.
    """
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE type = ? AND status IN ('queued', 'running')"
        " AND json_extract(payload, '$.source_id') = ? LIMIT 1",
        (job_type, source_id),
    ).fetchone()
    return row is not None


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


def get_failed_jobs_for_source(
    conn: sqlite3.Connection, source_id: str
) -> list[sqlite3.Row]:
    """Return all failed jobs for a source_id.

    Queries the payload's source_id field to match against the source_id
    parameter, using json_extract so that a source_id that is a prefix of
    another's (e.g. 'src-1' vs 'src-10') cannot false-match.
    """
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status='failed'"
        " AND json_extract(payload, '$.source_id') = ?",
        (source_id,),
    ).fetchall()
    return rows
