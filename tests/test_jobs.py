import json
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from splitstep.db import jobs as jobq
from splitstep.db.jobs import (
    claim,
    enqueue,
    enqueue_once,
    enqueue_reel_once,
    finish,
    has_pending_job,
    heartbeat,
    pending_reel_job,
    reclaim_stale,
    set_progress,
)
from splitstep.db.schema import connect, migrate
from splitstep.jobs.worker import Worker


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
    worker = Worker(library, handlers={"ingest": lambda lib, payload, _progress: seen.append(payload)})
    enqueue(conn, "ingest", {"path": "x.mov"})

    assert worker.run_once() is True
    assert seen == [{"path": "x.mov"}]


def test_worker_run_once_returns_false_when_idle(library):
    worker = Worker(library, handlers={})
    assert worker.run_once() is False


def test_worker_records_handler_exceptions_as_failures(library, conn):
    def boom(lib, payload, _progress):
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


def test_worker_heartbeats_a_running_job_on_a_timer(library, conn):
    """Before this fix, heartbeat_at froze at claim time -- reclaim_stale()
    could not tell an abandoned job from one partway through a long-running
    handler, so a second `splitstep serve` instance against the same library
    would requeue and double-run still-live work. This drives a handler that
    outlives several heartbeat ticks and asserts heartbeat_at actually
    advances while it runs, and that the heartbeat thread is gone once the
    handler returns.
    """
    job_id = enqueue(conn, "ingest", {})
    seen: list[str | None] = []

    def slow_handler(_lib, _payload, _progress):
        for _ in range(4):
            time.sleep(0.03)
            row = conn.execute(
                "SELECT heartbeat_at FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            seen.append(row["heartbeat_at"])

    worker = Worker(library, {"ingest": slow_handler}, heartbeat_interval_s=0.01)
    assert worker.run_once() is True

    assert len(set(seen)) > 1, f"heartbeat_at never advanced during the job: {seen}"
    assert not any(
        t.name == "heartbeat" and t.is_alive() for t in threading.enumerate()
    ), "heartbeat thread leaked past the end of run_once()"


def test_heartbeat_thread_stops_when_the_handler_raises(library, conn):
    """The heartbeat timer must not leak a thread on the failure path either."""
    def boom(_lib, _payload, _progress):
        time.sleep(0.03)
        raise ValueError("boom")

    worker = Worker(library, {"ingest": boom}, heartbeat_interval_s=0.01)
    enqueue(conn, "ingest", {})
    assert worker.run_once() is True

    assert not any(
        t.name == "heartbeat" and t.is_alive() for t in threading.enumerate()
    )


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
    """A second machine running `splitstep worker` against the same library is an
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


def test_enqueue_reel_once_enqueues_when_nothing_pending(conn):
    job_id, already_running = enqueue_reel_once(conn, "reel-1")
    assert already_running is False
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["type"] == "reel"
    assert row["status"] == "queued"
    assert json.loads(row["payload"]) == {"reel_id": "reel-1"}


def test_enqueue_reel_once_returns_the_existing_job_on_a_second_call(conn):
    first_id, _ = enqueue_reel_once(conn, "reel-1")
    second_id, already_running = enqueue_reel_once(conn, "reel-1")
    assert second_id == first_id
    assert already_running is True
    assert conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 1


def test_enqueue_reel_once_does_not_confuse_two_reels(conn):
    # Matched via json_extract, so one reel's render can never suppress
    # another's -- the same reason has_pending_clip exists beside
    # has_pending_job.
    first_id, _ = enqueue_reel_once(conn, "reel-1")
    other_id, already_running = enqueue_reel_once(conn, "reel-2")
    assert other_id != first_id
    assert already_running is False


def test_enqueue_reel_once_ignores_a_finished_render(conn):
    # A completed (or failed) render must not block a re-render -- only
    # 'queued'/'running' count as "in flight".
    job_id, _ = enqueue_reel_once(conn, "reel-1")
    finish(conn, job_id)
    new_id, already_running = enqueue_reel_once(conn, "reel-1")
    assert new_id != job_id
    assert already_running is False


def test_pending_reel_job_is_none_when_nothing_queued(conn):
    assert pending_reel_job(conn, "reel-1") is None


def test_pending_reel_job_finds_a_queued_job(conn):
    job_id, _ = enqueue_reel_once(conn, "reel-1")
    assert pending_reel_job(conn, "reel-1")["id"] == job_id


def test_pending_reel_job_finds_a_running_job(conn):
    job_id, _ = enqueue_reel_once(conn, "reel-1")
    conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job_id,))
    conn.commit()
    assert pending_reel_job(conn, "reel-1")["id"] == job_id


def test_pending_reel_job_ignores_a_finished_job(conn):
    job_id, _ = enqueue_reel_once(conn, "reel-1")
    finish(conn, job_id)
    assert pending_reel_job(conn, "reel-1") is None


def test_pending_reel_job_does_not_confuse_two_reels(conn):
    # Matched via json_extract, same as enqueue_reel_once's own check --
    # api_delete_reel must not refuse deleting reel-2 because reel-1 has a
    # render in flight.
    enqueue_reel_once(conn, "reel-1")
    assert pending_reel_job(conn, "reel-2") is None


def _enqueue_reel_once_after_barrier(db_path, idx, barrier, results):
    """Race helper for test_enqueue_reel_once_is_atomic_across_connections.

    Defined at module scope (not nested in the test's loop) so it never closes
    over a loop variable -- every input it needs is an explicit argument.
    """
    worker_conn = connect(db_path)
    try:
        barrier.wait()
        job_id, already_running = enqueue_reel_once(worker_conn, "reel-1")
        results[idx] = (job_id, already_running)
    finally:
        worker_conn.close()


def test_enqueue_reel_once_is_atomic_across_connections(library):
    """Reproduces the race Finding 1 describes: api_render_reel used to run a
    SELECT for an in-flight 'reel' job and a separate INSERT with no lock
    between them, and every API route gets its own thread and its own sqlite
    connection (ThreadLocalConnections), so two concurrent renders of the
    same reel could each pass the SELECT and each INSERT. This drives two
    separate connections at a barrier, mirroring what two Starlette worker
    threads actually get -- a TestClient-based test cannot reach this, since
    TestClient issues requests sequentially on one thread.
    """
    setup_conn = connect(library.db_path)
    migrate(setup_conn)
    try:
        for _ in range(30):
            barrier = threading.Barrier(2)
            results = [None, None]

            t1 = threading.Thread(
                target=_enqueue_reel_once_after_barrier,
                args=(library.db_path, 0, barrier, results),
            )
            t2 = threading.Thread(
                target=_enqueue_reel_once_after_barrier,
                args=(library.db_path, 1, barrier, results),
            )
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            assert results[0][0] == results[1][0], f"two distinct jobs: {results}"
            in_flight = ("running", "queued")
            count = setup_conn.execute(
                "SELECT COUNT(*) c FROM jobs WHERE type = 'reel' AND status IN (?, ?)"
                " AND json_extract(payload, '$.reel_id') = 'reel-1'",
                in_flight,
            ).fetchone()["c"]
            assert count == 1, f"expected exactly one in-flight reel job, found {count}"

            # Clean up so the next iteration starts from an empty queue.
            setup_conn.execute("DELETE FROM jobs WHERE type = 'reel'")
            setup_conn.commit()
    finally:
        setup_conn.close()


# -- has_pending_job: guards the duplicate-detect defect described in
# task-6-report.md -- reclaim_stale() can requeue build_proxy if a worker
# dies after it enqueues 'detect' but before its own job row is marked
# done, and a second 'detect' would silently discard hand-edited rally
# boundaries (replace_rallies only preserves starred/rejected by overlap).

def test_has_pending_job_true_when_a_queued_job_matches(conn):
    enqueue(conn, "detect", {"source_id": "src-1"})
    assert has_pending_job(conn, "detect", "src-1") is True


def test_has_pending_job_true_when_a_running_job_matches(conn):
    enqueue(conn, "detect", {"source_id": "src-1"})
    claim(conn)
    assert has_pending_job(conn, "detect", "src-1") is True


def test_has_pending_job_false_when_the_only_match_is_done(conn):
    job_id = enqueue(conn, "detect", {"source_id": "src-1"})
    claim(conn)
    finish(conn, job_id)
    assert has_pending_job(conn, "detect", "src-1") is False


def test_has_pending_job_false_when_the_only_match_is_failed(conn):
    job_id = enqueue(conn, "detect", {"source_id": "src-1"})
    claim(conn)
    finish(conn, job_id, error="ffmpeg exploded")
    assert has_pending_job(conn, "detect", "src-1") is False


def test_has_pending_job_ignores_a_different_job_type(conn):
    enqueue(conn, "build_proxy", {"source_id": "src-1"})
    assert has_pending_job(conn, "detect", "src-1") is False


def test_has_pending_job_ignores_a_different_source_id(conn):
    enqueue(conn, "detect", {"source_id": "src-2"})
    assert has_pending_job(conn, "detect", "src-1") is False


def test_has_pending_job_does_not_match_on_a_source_id_prefix(conn):
    """json_extract compares the decoded field, not raw payload text -- a
    naive substring/LIKE check on the payload string would wrongly treat a
    job queued for 'src-10' as a match for source_id 'src-1'.
    """
    enqueue(conn, "detect", {"source_id": "src-10"})
    assert has_pending_job(conn, "detect", "src-1") is False


def test_has_pending_job_false_when_the_queue_is_empty(conn):
    assert has_pending_job(conn, "detect", "src-1") is False


def test_worker_records_a_handlers_progress_on_the_job_row(library, conn):
    """The wiring that makes jobs.progress mean something. The handler is
    handed a reporter and knows nothing about job ids or the queue; the
    worker is the only place that knows which row is being worked on.
    """
    job_id = enqueue(conn, "ingest", {})

    def handler(_lib, _payload, progress):
        progress(0.25)
        progress(1.0)

    worker = Worker(library, {"ingest": handler})
    assert worker.run_once() is True
    row = conn.execute("SELECT status, progress FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "done"
    assert row["progress"] == pytest.approx(1.0)


def test_worker_progress_is_visible_while_the_job_is_still_running(library, conn):
    """A fraction only written once the job finishes is a fraction nobody
    ever sees. Each report commits, so a reader on another connection --
    which is every API request -- can watch it move."""
    job_id = enqueue(conn, "ingest", {})
    mid_run: list[float] = []

    def handler(_lib, _payload, progress):
        progress(0.5)
        mid_run.append(conn.execute(
            "SELECT progress FROM jobs WHERE id=?", (job_id,)
        ).fetchone()["progress"])

    Worker(library, {"ingest": handler}).run_once()
    assert mid_run == [pytest.approx(0.5)]


def test_enqueue_once_inserts_when_nothing_is_pending(conn):
    job_id = enqueue_once(conn, "detect", "src-1", {"source_id": "src-1"})
    assert job_id is not None
    assert has_pending_job(conn, "detect", "src-1")


def test_finish_stores_sentence_and_detail_separately(conn):
    job_id = enqueue(conn, "detect", {"source_id": "s"})
    finish(conn, job_id, error="short and human", error_detail="Traceback...")
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert row["error"] == "short and human"
    assert row["error_detail"] == "Traceback..."


def test_worker_failure_yields_a_sentence_not_a_traceback(library, conn):
    def explode(_library, _payload, _progress):
        raise ValueError("the proxy is missing")

    enqueue(conn, "boom", {"source_id": "s"})
    Worker(library, {"boom": explode}).run_once()
    row = conn.execute("SELECT * FROM jobs WHERE type='boom'").fetchone()
    assert row["error"] == "the proxy is missing"
    assert "Traceback" in row["error_detail"]


def test_enqueue_once_returns_none_when_a_job_is_already_pending(conn):
    first = enqueue_once(conn, "detect", "src-1", {"source_id": "src-1"})
    second = enqueue_once(conn, "detect", "src-1", {"source_id": "src-1"})
    assert first is not None and second is None
    rows = conn.execute("SELECT COUNT(*) c FROM jobs WHERE type='detect'").fetchone()
    assert rows["c"] == 1


def test_enqueue_once_does_not_match_a_prefix_source_id(conn):
    enqueue_once(conn, "detect", "src-1", {"source_id": "src-1"})
    assert enqueue_once(conn, "detect", "src-10", {"source_id": "src-10"}) is not None


def _enqueue_once_after_barrier(db_path, idx, barrier, results):
    """Race helper for test_enqueue_once_is_atomic_across_connections.

    Defined at module scope (not nested in the test's loop) so it never closes
    over a loop variable -- every input it needs is an explicit argument.
    """
    from splitstep.db.schema import connect

    worker_conn = connect(db_path)
    try:
        barrier.wait()
        job_id = enqueue_once(worker_conn, "detect", "src-1", {"source_id": "src-1"})
        results[idx] = job_id
    finally:
        worker_conn.close()


def test_enqueue_once_is_atomic_across_connections(library):
    """Reproduces the race the TODO describes: handle_build_proxy used to run a
    SELECT for an in-flight 'detect' job and a separate INSERT with no lock
    between them, and every serve process gets its own thread and its own sqlite
    connection, so two concurrent detects of the same source could each pass the
    SELECT and each INSERT. This drives two separate connections at a barrier,
    mirroring what two concurrent serve processes actually get.
    BEGIN IMMEDIATE closes this: the write lock serializes the check-and-insert,
    so only one thread succeeds and the other sees the pending job.
    """
    from splitstep.db.schema import connect, migrate

    setup_conn = connect(library.db_path)
    migrate(setup_conn)
    try:
        for _ in range(30):
            barrier = threading.Barrier(2)
            results = [None, None]

            t1 = threading.Thread(
                target=_enqueue_once_after_barrier,
                args=(library.db_path, 0, barrier, results),
            )
            t2 = threading.Thread(
                target=_enqueue_once_after_barrier,
                args=(library.db_path, 1, barrier, results),
            )
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            # One thread should have gotten a job_id, the other None
            assert (results[0] is not None and results[1] is None) or (
                results[0] is None and results[1] is not None
            ), f"expected one success and one None, got {results}"
            # Exactly one detect job for src-1 should exist
            row = setup_conn.execute(
                "SELECT COUNT(*) c FROM jobs WHERE type='detect'"
                " AND json_extract(payload, '$.source_id') = 'src-1'"
            ).fetchone()
            assert row["c"] == 1, f"expected 1 detect job, found {row['c']}"
            # Clean up for next iteration
            setup_conn.execute("DELETE FROM jobs")
            setup_conn.commit()
    finally:
        setup_conn.close()


def test_retry_requeues_a_failed_job_and_clears_its_error(conn):
    job_id = jobq.enqueue(conn, "detect", {"source_id": "s"})
    jobq.finish(conn, job_id, error="boom", error_detail="Traceback...")
    assert jobq.retry(conn, job_id) is True
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "queued"
    assert row["error"] is None and row["error_detail"] is None
    assert row["finished_at"] is None and row["heartbeat_at"] is None


def test_retry_refuses_a_job_that_did_not_fail(conn):
    job_id = jobq.enqueue(conn, "detect", {"source_id": "s"})
    assert jobq.retry(conn, job_id) is False
