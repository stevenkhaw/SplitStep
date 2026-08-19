import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from bootleg.config import Library
from bootleg.db import jobs as jobq
from bootleg.db.schema import connect, migrate

log = logging.getLogger(__name__)

VIDEO_SUFFIXES = frozenset({".mov", ".mp4", ".m4v", ".avi", ".mkv"})
SCAN_INTERVAL_S = 5.0


def is_stable(path: Path, settle_s: float = 3.0, poll_s: float = 0.5) -> bool:
    """True once the file size has not changed for settle_s.

    A half-copied 10 GB file probes fine and ingests into a corrupt session.
    This check is the only thing preventing that.
    """
    deadline = time.monotonic() + settle_s * 4
    last = -1
    unchanged_for = 0.0

    while time.monotonic() < deadline:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size == last:
            unchanged_for += poll_s
            if unchanged_for >= settle_s:
                return True
        else:
            unchanged_for = 0.0
            last = size
        time.sleep(poll_s)
    return False


def _already_queued(conn: sqlite3.Connection, path: Path) -> bool:
    rows = conn.execute(
        "SELECT payload FROM jobs WHERE type='ingest' AND status IN ('queued','running')"
    ).fetchall()
    return any(json.loads(r["payload"]).get("path") == str(path) for r in rows)


def scan_inbox(
    library: Library, conn: sqlite3.Connection, *, settle_s: float = 3.0
) -> list[str]:
    enqueued: list[str] = []
    for path in sorted(library.inbox.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        if _already_queued(conn, path):
            continue
        if not is_stable(path, settle_s=settle_s, poll_s=min(0.5, settle_s / 2)):
            log.info("still copying, skipping this pass: %s", path.name)
            continue
        enqueued.append(jobq.enqueue(conn, "ingest", {"path": str(path)}))
    return enqueued


class InboxWatcher:
    """Polls rather than using inotify — network and USB volumes fire
    filesystem events unreliably, and a 5 second poll is free."""

    def __init__(self, library: Library):
        self.library = library
        self.conn = connect(library.db_path)
        migrate(self.conn)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                scan_inbox(self.library, self.conn)
            except Exception:  # one bad scan must not kill the watcher thread
                log.exception("inbox scan failed")
            self._stop.wait(SCAN_INTERVAL_S)

    def start(self) -> None:
        # No mkdir here: `bootleg init` owns creating the tree. Auto-creating
        # part of it would recreate the exact hazard Finding 3 closed --
        # this thread starting happily against a library that was never
        # actually initialized.
        self._thread = threading.Thread(target=self._loop, daemon=True, name="inbox")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
