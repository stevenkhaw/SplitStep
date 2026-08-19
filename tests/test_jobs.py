import json
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from bootleg.db.jobs import (
    claim,
    enqueue,
    finish,
    heartbeat,
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


def test_heartbeat_advances_heartbeat_at(conn):
    job_id = enqueue(conn, "ingest", {})
    claim(conn)
    before = conn.execute(
        "SELECT heartbeat_at FROM jobs WHERE id=?", (job_id,)
    ).fetchone()["heartbeat_at"]

    time.sleep(0.01)
    heartbeat(conn, job_id)

    after = conn.execute(
        "SELECT heartbeat_at FROM jobs WHERE id=?", (job_id,)
    ).fetchone()["heartbeat_at"]
    assert after > before


def _claim_after_barrier(db_path, idx, barrier, results):
    """Race helper for test_claim_is_atomic_across_connections.

    Defined at module scope (not nested in the test's loop) so it never closes
    over a loop variable -- every input it needs is an explicit argument.
    """
    worker_conn = connect(db_path)
    try:
        barrier.wait()
        row = claim(worker_conn)
        results[idx] = row["id"] if row else None
    finally:
        worker_conn.close()


def test_claim_is_atomic_across_connections(library):
    """A second machine running `bootleg worker` against the same library is an
    explicitly designed-for deployment (see task-10-brief.md's discussion of
    reclaim_stale and stale heartbeats) -- this is the code that breaks first.
    Before claim() took the write lock with BEGIN IMMEDIATE, two connections
    racing on a barrier reproduced a double-claim on 100% of trials; this
    guards against that regressing.
    """
    setup_conn = connect(library.db_path)
    migrate(setup_conn)
    try:
        for _ in range(30):
            job_id = enqueue(setup_conn, "ingest", {})
            barrier = threading.Barrier(2)
            results = [None, None]

            t1 = threading.Thread(
                target=_claim_after_barrier, args=(library.db_path, 0, barrier, results)
            )
            t2 = threading.Thread(
                target=_claim_after_barrier, args=(library.db_path, 1, barrier, results)
            )
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            claimed_by = [r for r in results if r is not None]
            assert len(claimed_by) <= 1, f"both connections claimed {job_id}: {results}"
            if len(claimed_by) == 2:
                assert claimed_by[0] != claimed_by[1]
    finally:
        setup_conn.close()
