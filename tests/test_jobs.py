import json
from datetime import UTC, datetime, timedelta

import pytest

from bootleg.db.jobs import (
    claim,
    enqueue,
    finish,
    reclaim_stale,
    set_progress,
)
from bootleg.db.schema import connect, migrate
from bootleg.jobs.worker import Worker


@pytest.fixture
def conn(library):
    c = connect(library.db_path)
    migrate(c)
    return c


def test_enqueue_then_claim_returns_the_job(conn):
    job_id = enqueue(conn, "ingest", {"path": "/tmp/a.mov"})
    job = claim(conn)
    assert job["id"] == job_id
    assert job["status"] == "running"
    assert json.loads(job["payload"])["path"] == "/tmp/a.mov"


def test_claim_returns_none_when_queue_empty(conn):
    assert claim(conn) is None


def test_claim_is_fifo(conn):
    first = enqueue(conn, "ingest", {"n": 1})
    enqueue(conn, "ingest", {"n": 2})
    assert claim(conn)["id"] == first


def test_claim_does_not_return_running_jobs(conn):
    enqueue(conn, "ingest", {})
    claim(conn)
    assert claim(conn) is None


def test_finish_marks_done(conn):
    job_id = enqueue(conn, "ingest", {})
    claim(conn)
    finish(conn, job_id)
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "done"
    assert row["finished_at"] is not None


def test_finish_with_error_marks_failed_and_stores_message(conn):
    job_id = enqueue(conn, "ingest", {})
    claim(conn)
    finish(conn, job_id, error="ffmpeg exploded")
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert "exploded" in row["error"]


def test_reclaim_stale_requeues_abandoned_jobs(conn):
    job_id = enqueue(conn, "ingest", {})
    claim(conn)
    old = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    conn.execute("UPDATE jobs SET heartbeat_at=? WHERE id=?", (old, job_id))
    conn.commit()

    assert reclaim_stale(conn, older_than_s=120) == 1
    assert conn.execute(
        "SELECT status FROM jobs WHERE id=?", (job_id,)
    ).fetchone()["status"] == "queued"


def test_reclaim_leaves_fresh_jobs_alone(conn):
    enqueue(conn, "ingest", {})
    claim(conn)
    assert reclaim_stale(conn, older_than_s=120) == 0


def test_set_progress_updates_the_row(conn):
    job_id = enqueue(conn, "ingest", {})
    claim(conn)
    set_progress(conn, job_id, 0.42)
    assert conn.execute(
        "SELECT progress FROM jobs WHERE id=?", (job_id,)
    ).fetchone()["progress"] == pytest.approx(0.42)


def test_worker_run_once_dispatches_to_the_handler(library, conn):
    seen = []
    worker = Worker(library, handlers={"ingest": lambda lib, payload: seen.append(payload)})
    enqueue(conn, "ingest", {"path": "x.mov"})

    assert worker.run_once() is True
    assert seen == [{"path": "x.mov"}]


def test_worker_run_once_returns_false_when_idle(library):
    worker = Worker(library, handlers={})
    assert worker.run_once() is False


def test_worker_records_handler_exceptions_as_failures(library, conn):
    def boom(lib, payload):
        raise ValueError("nope")

    worker = Worker(library, handlers={"ingest": boom})
    job_id = enqueue(conn, "ingest", {})
    worker.run_once()

    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert "nope" in row["error"]


def test_worker_fails_unknown_job_types(library, conn):
    worker = Worker(library, handlers={})
    job_id = enqueue(conn, "mystery", {})
    worker.run_once()
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert "mystery" in row["error"]
