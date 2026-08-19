import json
import subprocess

import pytest

from bootleg.db.rallies import list_rallies
from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import list_sessions, list_sources
from bootleg.detect.features import FeatureFrame, Player, write_features
from bootleg.jobs.handlers import handle_detect, handle_ingest


@pytest.fixture
def conn(library):
    c = connect(library.db_path)
    migrate(c)
    return c


@pytest.fixture
def dropped_video(library):
    out = library.inbox / "IMG_0001.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=640x360:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_ingest_creates_session_source_and_proxy(library, conn, dropped_video):
    handle_ingest(library, {"path": str(dropped_video)})

    sessions = list_sessions(conn)
    assert len(sessions) == 1

    sources = list_sources(conn, sessions[0]["id"])
    assert len(sources) == 1
    assert sources[0]["original_name"] == "IMG_0001.mp4"

    src_dir = library.source_dir(sessions[0]["id"], 1)
    assert (src_dir / "proxy.mp4").exists()
    assert (src_dir / "thumbs.jpg").exists()
    assert (src_dir / "original.mp4").exists()


def test_ingest_removes_the_file_from_the_inbox(library, conn, dropped_video):
    handle_ingest(library, {"path": str(dropped_video)})
    assert not dropped_video.exists()


def test_ingest_enqueues_a_detect_job(library, conn, dropped_video):
    handle_ingest(library, {"path": str(dropped_video)})
    row = conn.execute("SELECT * FROM jobs WHERE type='detect'").fetchone()
    assert row is not None
    assert "source_id" in json.loads(row["payload"])


def test_second_file_same_day_joins_the_same_session(library, conn, dropped_video):
    handle_ingest(library, {"path": str(dropped_video)})
    second = library.inbox / "IMG_0002.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=640x360:rate=30:duration=2",
         "-c:v", "libx264", str(second)],
        check=True, capture_output=True,
    )
    handle_ingest(library, {"path": str(second)})

    sessions = list_sessions(conn)
    assert len(sessions) == 1
    assert len(list_sources(conn, sessions[0]["id"])) == 2


def test_detect_segments_from_cached_features(library, conn, dropped_video, monkeypatch):
    handle_ingest(library, {"path": str(dropped_video)})
    session_id = list_sessions(conn)[0]["id"]
    source = list_sources(conn, session_id)[0]

    # Pre-write features so no YOLO is needed: 8 s of two-player activity.
    frames = [
        FeatureFrame(i * 200, 2,
                     Player(0.5, 0.9, 0.30, 2.5), Player(0.5, 0.4, 0.10, 2.5),
                     hits=1, hit_reg=0.9)
        for i in range(40)
    ]
    write_features(library.source_dir(session_id, 1) / "features.jsonl", frames)

    handle_detect(library, {"source_id": source["id"], "reuse_features": True})

    rallies = list_rallies(conn, session_id)
    assert len(rallies) == 1
    assert rallies[0]["det_start_ms"] == rallies[0]["start_ms"]


def test_detect_is_idempotent(library, conn, dropped_video):
    handle_ingest(library, {"path": str(dropped_video)})
    session_id = list_sessions(conn)[0]["id"]
    source = list_sources(conn, session_id)[0]

    frames = [
        FeatureFrame(i * 200, 2,
                     Player(0.5, 0.9, 0.30, 2.5), Player(0.5, 0.4, 0.10, 2.5),
                     hits=1, hit_reg=0.9)
        for i in range(40)
    ]
    write_features(library.source_dir(session_id, 1) / "features.jsonl", frames)

    handle_detect(library, {"source_id": source["id"], "reuse_features": True})
    handle_detect(library, {"source_id": source["id"], "reuse_features": True})
    assert len(list_rallies(conn, session_id)) == 1


def test_detect_is_idempotent_with_two_sources(library, conn, dropped_video):
    # A single-source session can never exercise a sibling's idx during
    # renumbering -- there is nothing else in the session to collide with.
    # This ingests two sources into one session and re-runs detect on the
    # first one after both already have rallies, so the renumber has to
    # pass over the second source's still-live idx values.
    handle_ingest(library, {"path": str(dropped_video)})
    second = library.inbox / "IMG_0002.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=640x360:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(second)],
        check=True, capture_output=True,
    )
    handle_ingest(library, {"path": str(second)})

    session_id = list_sessions(conn)[0]["id"]
    sources = list_sources(conn, session_id)
    assert len(sources) == 2

    frames = [
        FeatureFrame(i * 200, 2,
                     Player(0.5, 0.9, 0.30, 2.5), Player(0.5, 0.4, 0.10, 2.5),
                     hits=1, hit_reg=0.9)
        for i in range(40)
    ]
    for source in sources:
        write_features(
            library.source_dir(session_id, source["idx"]) / "features.jsonl", frames
        )

    for source in sources:
        handle_detect(library, {"source_id": source["id"], "reuse_features": True})
    # Re-run detect on the first source again while the second source's
    # rallies are still sitting on their previously assigned idx.
    handle_detect(library, {"source_id": sources[0]["id"], "reuse_features": True})

    rallies = list_rallies(conn, session_id)
    assert len(rallies) == 2
    idxs = [r["idx"] for r in rallies]
    assert sorted(idxs) == [1, 2]
