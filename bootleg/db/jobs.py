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


def has_pending_clip(
    conn: sqlite3.Connection, source_id: str, start_ms: int, end_ms: int
) -> bool:
    """True if a clip job for exactly this span is already queued or running.

    Separate from has_pending_job, which matches on source_id alone. That is
    right for ingest/build_proxy/detect, which are one-per-source, and wrong
    for clips: one source yields dozens, so a source-wide check would let the
    first enqueued clip suppress every other clip from the same session.
    """
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE type = 'clip' AND status IN ('queued', 'running')"
        " AND json_extract(payload, '$.source_id') = ?"
        " AND json_extract(payload, '$.start_ms') = ?"
        " AND json_extract(payload, '$.end_ms') = ? LIMIT 1",
        (source_id, start_ms, end_ms),
    ).fetchone()
    return row is not None


def pending_reel_job(conn: sqlite3.Connection, reel_id: str) -> sqlite3.Row | None:
    """The queued or running 'reel' job for `reel_id`, if there is one.

    Two callers, one query. `enqueue_reel_once` needs the row (for its
    `job_id`) to make a second render click return the in-flight job rather
    than queue a duplicate; `api_delete_reel` needs only to know one exists,
    to refuse a delete that would otherwise race `concat_clips` and orphan
    the file it is about to `os.replace()` into place (`mark_rendered`'s
    UPDATE would match zero rows once the row is gone -- correctly not
    resurrecting a deleted reel, but leaving that file with nothing
    referencing it). Extracted rather than inlined a second time: this
    project already removed one duplicate of this exact query once, back
    when `enqueue_reel_once` was its only caller.
    """
    return conn.execute(
        "SELECT id FROM jobs WHERE type = 'reel' AND status IN ('queued', 'running')"
        " AND json_extract(payload, '$.reel_id') = ? LIMIT 1",
        (reel_id,),
    ).fetchone()


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


def enqueue_reel_once(conn: sqlite3.Connection, reel_id: str) -> tuple[str, bool]:
    """Return (job_id, already_running): the in-flight render for `reel_id`
    if one exists, else a freshly enqueued one.

    Same shape as claim() and for the same reason: a plain "SELECT for an
    in-flight job, then INSERT if none" is a check-then-act race, since every
    API route runs on its own thread with its own sqlite connection
    (ThreadLocalConnections). Two concurrent renders of the same reel could
    each run the SELECT, each see nothing, and each INSERT -- exactly the gap
    BEGIN IMMEDIATE closes by taking the write lock before the SELECT runs
    (a bare `with conn:` would not: legacy sqlite3 isolation defers BEGIN
    until the first DML statement, so the SELECT would still execute in
    autocommit mode). The INSERT is written inline rather than via enqueue()
    so the whole check-and-insert commits exactly once, under the one lock,
    rather than enqueue() taking a second implicit transaction of its own.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = pending_reel_job(conn, reel_id)
        if existing is not None:
            conn.commit()
            return existing["id"], True
        job_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO jobs (id,type,payload,status,created_at)"
            " VALUES (?,?,?,'queued',?)",
            (job_id, "reel", json.dumps({"reel_id": reel_id}), _now()),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return job_id, False


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
    conn: sqlite3.Connection, source_id: str, since: str | None = None
) -> list[sqlite3.Row]:
    """Return failed jobs for a source_id, optionally scoped to `since`.

    Queries the payload's source_id field to match against the source_id
    parameter, using json_extract so that a source_id that is a prefix of
    another's (e.g. 'src-1' vs 'src-10') cannot false-match.

    `since` (an ISO timestamp, as produced by `_now()`) restricts the
    result to jobs created at or after it. Without it, a caller like
    `bootleg setup --now` would report failure -- and exit non-zero --
    forever after a single failed run, even once a later run of the very
    same source's jobs succeeds outright: the source's job history is
    cumulative, but "did THIS invocation's jobs succeed" is a question
    about a specific time window, not the source's entire history.
    """
    query = "SELECT * FROM jobs WHERE status='failed' AND json_extract(payload, '$.source_id') = ?"
    params: list[str] = [source_id]
    if since is not None:
        query += " AND created_at >= ?"
        params.append(since)
    return conn.execute(query, params).fetchall()
