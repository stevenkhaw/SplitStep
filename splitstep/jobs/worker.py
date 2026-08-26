import json
import logging
import sqlite3
import threading
import traceback
from collections.abc import Callable
from typing import Self

from splitstep.config import Library
from splitstep.db import jobs as jobq
from splitstep.db.schema import connect, migrate
from splitstep.media.transcode import ProgressFn

log = logging.getLogger(__name__)

# Handlers are handed a reporter rather than their own job id: a handler
# that knew the id would also have to know the queue's schema to use it, and
# the only thing it actually has to say is "this fraction of me is done".
# The CLI and the tests call handlers directly, with no row behind them, so
# every handler defaults this to `no_progress` and none of them branch on it.
Handler = Callable[[Library, dict, ProgressFn], None]
POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 30.0


def no_progress(_fraction: float) -> None:
    """The reporter a handler gets when nothing is listening.

    A no-op rather than None so no handler has to guard the call, and
    so a direct caller (the CLI, a test) never has to invent one.
    """


def _error_summary(exc: BaseException) -> str:
    """One line a reviewer can act on, not a stack.

    Handlers already raise with good sentences (TranscodeError's colour
    message, ValueError's "No proxy on disk for source …"); str(exc) is
    that sentence. The class name is the fallback for exceptions raised
    bare, and the truncation guards against an exception whose repr is a
    payload dump.
    """
    text = str(exc).strip() or type(exc).__name__
    first_line = text.splitlines()[0]
    return first_line[:300]


class _Heartbeat:
    """Ticks `jobs.heartbeat_at` on a timer for the duration of a handler call.

    Without this, heartbeat_at freezes at claim time and reclaim_stale()
    cannot tell an abandoned job from one three minutes into a fifteen-minute
    detect -- a second `splitstep serve` instance against the same library
    would requeue and duplicate-run still-live work. The thread is started
    and stopped around exactly one handler invocation, via context manager,
    so it cannot outlive the call whether the handler returns or raises.
    """

    def __init__(
        self, conn: sqlite3.Connection, job_id: str, interval_s: float = HEARTBEAT_SECONDS
    ):
        self._conn = conn
        self._job_id = job_id
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="heartbeat")

    def _run(self) -> None:
        while not self._stop.wait(self._interval_s):
            try:
                jobq.heartbeat(self._conn, self._job_id)
            except Exception:
                log.exception("heartbeat failed for job %s", self._job_id)

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5.0)


class Worker:
    """Claims one job at a time. All handlers are idempotent, so retry is safe."""

    def __init__(
        self,
        library: Library,
        handlers: dict[str, Handler],
        *,
        heartbeat_interval_s: float = HEARTBEAT_SECONDS,
    ):
        self.library = library
        self.handlers = handlers
        self.heartbeat_interval_s = heartbeat_interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.conn = connect(library.db_path)
        migrate(self.conn)

    def run_once(self) -> bool:
        job = jobq.claim(self.conn)
        if job is None:
            return False

        handler = self.handlers.get(job["type"])
        if handler is None:
            # No handler means no traceback either -- there is nothing for
            # error_detail to keep.
            jobq.finish(self.conn, job["id"], error=f"Unknown job type: {job['type']}")
            return True

        def report(fraction: float) -> None:
            # Same connection the heartbeat thread ticks, and set_progress
            # writes heartbeat_at too -- so a handler that reports at all
            # cannot be mistaken for an abandoned job even if the timer
            # thread were to die.
            jobq.set_progress(self.conn, job["id"], fraction)

        try:
            with _Heartbeat(self.conn, job["id"], self.heartbeat_interval_s):
                handler(self.library, json.loads(job["payload"]), report)
        except Exception as exc:
            jobq.finish(
                self.conn, job["id"],
                error=_error_summary(exc),
                error_detail=traceback.format_exc(limit=6),
            )
            log.exception("job %s failed", job["id"])
        else:
            jobq.finish(self.conn, job["id"])
        return True

    def _loop(self) -> None:
        jobq.reclaim_stale(self.conn)
        while not self._stop.is_set():
            try:
                if not self.run_once():
                    self._stop.wait(POLL_SECONDS)
            except Exception:
                log.exception("worker loop error")
                self._stop.wait(POLL_SECONDS)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="worker")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
