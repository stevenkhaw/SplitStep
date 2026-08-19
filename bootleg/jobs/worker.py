import json
import logging
import threading
import traceback
from collections.abc import Callable

from bootleg.config import Library
from bootleg.db import jobs as jobq
from bootleg.db.schema import connect, migrate

log = logging.getLogger(__name__)

Handler = Callable[[Library, dict], None]
POLL_SECONDS = 1.0


class Worker:
    """Claims one job at a time. All handlers are idempotent, so retry is safe."""

    def __init__(self, library: Library, handlers: dict[str, Handler]):
        self.library = library
        self.handlers = handlers
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
            jobq.finish(self.conn, job["id"], error=f"Unknown job type: {job['type']}")
            return True

        try:
            handler(self.library, json.loads(job["payload"]))
        except Exception:
            jobq.finish(self.conn, job["id"], error=traceback.format_exc(limit=6))
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
