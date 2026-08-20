import threading
import time

import pytest

from bootleg.db.schema import connect, migrate
from bootleg.watcher import is_stable, scan_inbox


@pytest.fixture
def conn(library):
    c = connect(library.db_path)
    migrate(c)
    return c


def test_is_stable_true_for_a_settled_file(library):
    f = library.inbox / "a.mov"
    f.write_bytes(b"x" * 1024)
    assert is_stable(f, settle_s=0.3, poll_s=0.1) is True


def test_is_stable_false_while_a_file_is_still_growing(library):
    f = library.inbox / "growing.mov"
    f.write_bytes(b"x" * 1024)
    stop = threading.Event()

    def grow():
        while not stop.is_set():
            with f.open("ab") as fh:
                fh.write(b"y" * 4096)
            time.sleep(0.05)

    t = threading.Thread(target=grow, daemon=True)
    t.start()
    try:
        assert is_stable(f, settle_s=0.4, poll_s=0.1) is False
    finally:
        stop.set()
        t.join()


def test_scan_enqueues_settled_videos(library, conn):
    (library.inbox / "a.mov").write_bytes(b"x" * 1024)
    ids = scan_inbox(library, conn, settle_s=0.2)
    assert len(ids) == 1
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (ids[0],)).fetchone()
    assert row["type"] == "ingest"


def test_scan_ignores_non_video_files(library, conn):
    (library.inbox / "notes.txt").write_text("hello")
    (library.inbox / ".DS_Store").write_bytes(b"junk")
    assert scan_inbox(library, conn, settle_s=0.2) == []


def test_scan_does_not_enqueue_the_same_file_twice(library, conn):
    (library.inbox / "a.mov").write_bytes(b"x" * 1024)
    scan_inbox(library, conn, settle_s=0.2)
    assert scan_inbox(library, conn, settle_s=0.2) == []


def test_scan_accepts_uppercase_suffixes(library, conn):
    (library.inbox / "IMG_0001.MOV").write_bytes(b"x" * 1024)
    assert len(scan_inbox(library, conn, settle_s=0.2)) == 1
