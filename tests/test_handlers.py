import json
import subprocess

import pytest

from bootleg.db.rallies import list_rallies
from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import list_sessions, list_sources
from bootleg.detect.features import FeatureFrame, Player, write_features
from bootleg.jobs import handlers
from bootleg.jobs.handlers import handle_detect, handle_ingest
from bootleg.media.probe import ProbeError
from bootleg.media.transcode import TranscodeError
from bootleg.watcher import scan_inbox


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


# -- Finding 4: a bad file must not re-enqueue forever, and failures must be
# recorded as 'failed' rather than silently freezing at 'ingesting' ---------

def test_an_unprobeable_file_is_quarantined_and_not_rescanned(library, conn):
    """Before this fix, a bad file that never gets past probe() stays in the
    inbox forever, filtered from _already_queued only while its ingest job
    is 'queued'/'running' -- once that job fails, the very next 5-second
    scan queues it again. Measured at 720 failed job rows per hour.
    """
    bad = library.inbox / "corrupt.mp4"
    bad.write_bytes(b"not a real video, just garbage bytes" * 5)

    with pytest.raises(ProbeError):
        handle_ingest(library, {"path": str(bad)})

    assert not bad.exists()
    quarantined = library.inbox / "failed" / "corrupt.mp4"
    assert quarantined.exists()
    assert (library.inbox / "failed" / "corrupt.mp4.error.txt").read_text()

    # A fresh scan must not find it as a top-level inbox file to re-queue.
    assert scan_inbox(library, conn, settle_s=0.1) == []


def test_an_encode_failure_marks_the_source_and_session_failed(
    library, conn, dropped_video, monkeypatch
):
    """Nothing ever set sources.status/sessions.status to 'failed' -- the
    value was dead in both schema comments, and spec's "ffprobe/encode
    failure marks the session failed" was unimplemented. This also proves
    the failing file is quarantined even after session/source rows exist.
    """
    def boom(src, dst, *_a, **_kw):
        raise TranscodeError("ffmpeg exploded")

    monkeypatch.setattr(handlers, "make_proxy", boom)

    with pytest.raises(TranscodeError):
        handle_ingest(library, {"path": str(dropped_video)})

    session = list_sessions(conn)[0]
    source = list_sources(conn, session["id"])[0]
    assert source["status"] == "failed"
    assert session["status"] == "failed"

    assert not dropped_video.exists()
    assert (library.inbox / "failed" / "IMG_0001.mp4").exists()
    assert scan_inbox(library, conn, settle_s=0.1) == []


# -- Finding 5: handle_ingest must be idempotent across a worker crash ------

class _SimulatedCrash(BaseException):
    """Stands in for the worker process dying mid-handler.

    Deliberately NOT an Exception subclass, so it propagates straight past
    handle_ingest's `except Exception` cleanup -- exactly like a real crash:
    no failed-dir move, no status flip, the job row is simply abandoned at
    'running' until reclaim_stale() requeues it with the same payload.
    """


def test_retry_after_a_crash_reuses_the_still_present_source_file(
    library, conn, dropped_video, monkeypatch
):
    """shutil.move used to relocate the inbox file to its final
    sessions/<id>/sources/<idx>/original.* path *before* encoding it. A
    crash after that move but before the job finished left the source row
    stuck at 'ingesting' forever: reclaim_stale() hands the exact same
    payload (the original inbox path) to a fresh handle_ingest() call, and
    probe() can never find the file there again.

    This simulates the crash landing after add_source() has committed but
    before the proxy is encoded, then replays the same payload -- the
    reclaim_stale() retry path -- and asserts it completes cleanly, reusing
    the already-committed source row instead of duplicating it.
    """
    payload = {"path": str(dropped_video)}
    calls = {"n": 0}
    real_make_proxy = handlers.make_proxy

    def crash_on_first_call(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _SimulatedCrash()
        return real_make_proxy(src, dst, *a, **kw)

    monkeypatch.setattr(handlers, "make_proxy", crash_on_first_call)

    with pytest.raises(_SimulatedCrash):
        handle_ingest(library, payload)

    # The crash landed before the move -- the file must still be exactly
    # where the payload says it is, or the retry below cannot find it.
    assert dropped_video.exists()
    session_after_crash = list_sessions(conn)[0]
    assert list_sources(conn, session_after_crash["id"])[0]["status"] == "ingesting"

    # reclaim_stale()'s retry: the same payload, handed to a fresh call.
    handle_ingest(library, payload)

    sessions = list_sessions(conn)
    assert len(sessions) == 1
    sources = list_sources(conn, sessions[0]["id"])
    assert len(sources) == 1  # reused, not duplicated by the retry
    assert sources[0]["status"] == "ingested"

    src_dir = library.source_dir(sessions[0]["id"], 1)
    assert (src_dir / "proxy.mp4").exists()
    assert (src_dir / "original.mp4").exists()
    assert not dropped_video.exists()
