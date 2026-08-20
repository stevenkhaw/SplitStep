import subprocess

import pytest
from fastapi.testclient import TestClient

from bootleg.api.app import create_app
from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import list_sessions, list_sources
from bootleg.detect.features import FeatureFrame, Player, write_features
from bootleg.jobs.handlers import HANDLERS
from bootleg.jobs.worker import Worker
from bootleg.watcher import scan_inbox


@pytest.fixture
def conn(library):
    c = connect(library.db_path)
    migrate(c)
    return c


def test_drop_file_then_serve_it_over_the_api(library, conn):
    # 1. a file lands in the inbox
    dropped = library.inbox / "IMG_0007.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=640x360:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(dropped)],
        check=True, capture_output=True,
    )

    # 2. the watcher queues it
    assert len(scan_inbox(library, conn, settle_s=0.2)) == 1

    # 3. the worker registers it -- register-only ingest probes and moves
    # the file but does not transcode it, and leaves nothing auto-queued:
    # a human confirms orientation and play region in the setup wizard
    # first, and that is what will enqueue build_proxy (a later task).
    worker = Worker(library, HANDLERS)
    assert worker.run_once() is True

    session = list_sessions(conn)[0]
    source = list_sources(conn, session["id"])[0]
    assert source["status"] == "needs_setup"
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE type='detect'").fetchone()[0] == 0

    src_dir = library.source_dir(session["id"], source["idx"])
    assert (src_dir / "original.mp4").exists()
    assert not (src_dir / "proxy.mp4").exists()

    # 4. stand in for the setup wizard's build_proxy + YOLO with fixtured
    # features, then run detect directly.
    write_features(src_dir / "features.jsonl", [
        FeatureFrame(i * 200, 2,
                     Player(0.5, 0.9, 0.30, 2.5), Player(0.5, 0.4, 0.10, 2.5),
                     hits=1, hit_reg=0.9)
        for i in range(40)
    ])
    HANDLERS["detect"](library, {"source_id": source["id"], "reuse_features": True})

    # 5. the API exposes the result
    with TestClient(create_app(library)) as client:
        body = client.get(f"/api/sessions/{session['id']}").json()
        assert len(body["rallies"]) == 1
