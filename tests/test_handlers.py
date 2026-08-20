import json
import subprocess
from types import SimpleNamespace

import pytest

from bootleg.db.presets import create_preset
from bootleg.db.rallies import list_rallies
from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import (
    add_source,
    create_session,
    get_source,
    list_sessions,
    list_sources,
    set_source_preset,
    set_source_rotation,
)
from bootleg.detect.features import FeatureFrame, Player, write_features
from bootleg.detect.geometry import Quad
from bootleg.jobs import handlers
from bootleg.jobs.handlers import handle_build_proxy, handle_detect, handle_ingest
from bootleg.media.probe import ProbeError, probe
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


@pytest.fixture
def sample_video(tmp_path):
    """2 second 320x240 30fps clip with a 440Hz tone."""
    out = tmp_path / "sample.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_ingest_creates_session_and_source_without_a_proxy(library, conn, dropped_video):
    handle_ingest(library, {"path": str(dropped_video)})

    sessions = list_sessions(conn)
    assert len(sessions) == 1

    sources = list_sources(conn, sessions[0]["id"])
    assert len(sources) == 1
    assert sources[0]["original_name"] == "IMG_0001.mp4"
    assert sources[0]["status"] == "needs_setup"

    src_dir = library.source_dir(sessions[0]["id"], 1)
    assert (src_dir / "original.mp4").exists()
    # Register-only ingest never transcodes -- the proxy and sprite sheet
    # are the setup wizard's build_proxy job, not ingest's.
    assert not (src_dir / "proxy.mp4").exists()
    assert not (src_dir / "thumbs.jpg").exists()


def test_ingest_removes_the_file_from_the_inbox(library, conn, dropped_video):
    handle_ingest(library, {"path": str(dropped_video)})
    assert not dropped_video.exists()


def test_ingest_no_longer_enqueues_a_detect_job(library, conn, dropped_video):
    handle_ingest(library, {"path": str(dropped_video)})
    row = conn.execute("SELECT * FROM jobs WHERE type='detect'").fetchone()
    assert row is None


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


# -- court presets: _quad_for -------------------------------------------------

def test_quad_for_returns_default_quad_when_no_preset_is_assigned(
    library, conn, dropped_video
):
    handle_ingest(library, {"path": str(dropped_video)})
    session_id = list_sessions(conn)[0]["id"]
    source = list_sources(conn, session_id)[0]
    assert handlers._quad_for(conn, source) == handlers.DEFAULT_QUAD


def test_quad_for_returns_the_assigned_preset_quad(library, conn, dropped_video):
    handle_ingest(library, {"path": str(dropped_video)})
    session_id = list_sessions(conn)[0]["id"]
    source = list_sources(conn, session_id)[0]

    quad = Quad(((0.1, 0.9), (0.9, 0.9), (0.7, 0.3), (0.3, 0.3)))
    preset_id = create_preset(conn, "backyard", quad)
    set_source_preset(conn, source["id"], preset_id)

    refreshed = get_source(conn, source["id"])
    assert handlers._quad_for(conn, refreshed) == quad
    assert handlers._quad_for(conn, refreshed) != handlers.DEFAULT_QUAD


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


def test_a_failure_after_the_source_is_created_marks_it_failed(
    library, conn, dropped_video, monkeypatch
):
    """Nothing ever set sources.status/sessions.status to 'failed' -- the
    value was dead in both schema comments, and spec's "failure marks the
    session failed" was unimplemented. This also proves the failing file is
    quarantined even after session/source rows exist. Register-only ingest
    no longer encodes, so the failure is simulated at the move into the
    session tree -- the step that now runs where the transcode used to.
    Only the first move call fails: the second is `_move_to_failed`'s own
    call to quarantine the file, which must still work with a real
    `shutil.move` or this test could not tell the two failure paths apart.
    """
    real_move = handlers.shutil.move
    calls = {"n": 0}

    def flaky_move(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk exploded")
        return real_move(src, dst, *a, **kw)

    monkeypatch.setattr(handlers.shutil, "move", flaky_move)

    with pytest.raises(OSError):
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


def test_retry_after_a_crash_before_the_move_reuses_the_committed_source_row(
    library, conn, dropped_video, monkeypatch
):
    """Register-only ingest moves the original right after add_source()
    commits, and handles a requeue whose inbox path is already gone by
    verifying the earlier move actually landed before reaffirming its
    status (see
    test_ingest_retry_after_the_original_moved_reaches_needs_setup_again).
    But a crash can still land *before* that move finishes, leaving the
    source row at 'ingesting' with the file still sitting at its original
    inbox path. reclaim_stale() hands the exact same payload to a fresh
    handle_ingest() call; this asserts the retry reuses the already-
    committed source row instead of duplicating it.
    """
    payload = {"path": str(dropped_video)}
    calls = {"n": 0}
    real_move = handlers.shutil.move

    def crash_on_first_call(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _SimulatedCrash()
        return real_move(src, dst, *a, **kw)

    monkeypatch.setattr(handlers.shutil, "move", crash_on_first_call)

    with pytest.raises(_SimulatedCrash):
        handle_ingest(library, payload)

    # The crash landed before the move completed -- the file must still be
    # exactly where the payload says it is, or the retry below cannot find it.
    assert dropped_video.exists()
    session_after_crash = list_sessions(conn)[0]
    assert list_sources(conn, session_after_crash["id"])[0]["status"] == "ingesting"

    # reclaim_stale()'s retry: the same payload, handed to a fresh call.
    handle_ingest(library, payload)

    sessions = list_sessions(conn)
    assert len(sessions) == 1
    sources = list_sources(conn, sessions[0]["id"])
    assert len(sources) == 1  # reused, not duplicated by the retry
    assert sources[0]["status"] == "needs_setup"

    src_dir = library.source_dir(sessions[0]["id"], 1)
    assert (src_dir / "original.mp4").exists()
    assert not dropped_video.exists()


# -- Fix: stranded source on crash between move and status write ------------

def test_retry_after_a_crash_between_the_move_and_the_status_write_reaches_needs_setup(
    library, conn, dropped_video, monkeypatch
):
    """Regression test for the defect this fix addresses. handle_ingest
    moves the original and only then writes 'needs_setup'; a crash in that
    window used to strand the source at add_source()'s 'ingesting' forever,
    because the early-return guard saw the inbox path gone and assumed the
    earlier attempt had already finished -- the false assumption this fix
    replaces with a real check. Simulated the way the reviewer reproduced
    it: set_source_status's first call with 'needs_setup' raises, standing
    in for a crash that lands after the move but before the status write.
    """
    payload = {"path": str(dropped_video)}
    real_set_source_status = handlers.set_source_status
    calls = {"n": 0}

    def crash_on_first_needs_setup(conn, source_id, status):
        if status == "needs_setup":
            calls["n"] += 1
            if calls["n"] == 1:
                raise _SimulatedCrash()
        return real_set_source_status(conn, source_id, status)

    monkeypatch.setattr(handlers, "set_source_status", crash_on_first_needs_setup)

    with pytest.raises(_SimulatedCrash):
        handle_ingest(library, payload)

    # The move already landed; only the status write was lost to the crash.
    assert not dropped_video.exists()
    session = list_sessions(conn)[0]
    assert list_sources(conn, session["id"])[0]["status"] == "ingesting"

    monkeypatch.setattr(handlers, "set_source_status", real_set_source_status)

    # reclaim_stale()'s retry: the same payload, inbox path now gone.
    handle_ingest(library, payload)

    sessions = list_sessions(conn)
    assert len(sessions) == 1
    sources = list_sources(conn, sessions[0]["id"])
    assert len(sources) == 1  # reused, not duplicated
    assert sources[0]["status"] == "needs_setup"
    assert sessions[0]["status"] == "needs_setup"


def test_retry_after_a_crash_does_not_claim_a_same_named_source_in_another_session(
    library, conn, dropped_video, monkeypatch
):
    """Two sessions can hold a source with the same original_name -- a
    phone reusing IMG_0001.MOV across days. Reproduces the reviewer's
    finding: a name-only recovery lookup has no session to scope by, so it
    can resolve to whichever same-named row insertion order happens to put
    first -- silently reaffirming an unrelated, already-finished session
    instead of the one that actually crashed, and leaving the real crashed
    source stranded at 'ingesting' forever. Session A is a complete,
    unrelated ingest sitting at 'needs_setup'; session B crashes between
    the move and the status write with the *same* original_name. Session
    B's retry must reach 'needs_setup' and session A's row must not move
    at all -- not just its status, nothing about it.
    """
    session_a = create_session(conn, "session-a", "2024-01-01", "2024-01-01")
    source_a_id, idx_a = add_source(
        conn, session_a,
        recorded_at="2024-01-01T00:00:00+00:00",
        duration_ms=2000, width=640, height=360, fps=30.0,
        original_name="IMG_0001.mp4",
    )
    handlers.set_source_status(conn, source_a_id, "needs_setup")
    handlers.set_session_status(conn, session_a, "needs_setup")
    a_dir = library.source_dir(session_a, idx_a)
    a_dir.mkdir(parents=True)
    (a_dir / "original.mp4").write_bytes(b"session a's original")
    source_a_before = dict(get_source(conn, source_a_id))

    # Session B: dropped_video is also named IMG_0001.mp4 (the fixture's
    # fixed name), so find_or_create_session_for_date -- keyed off today's
    # date, not session A's 2024-01-01 -- creates a second, distinct
    # session for it.
    payload = {"path": str(dropped_video)}
    real_set_source_status = handlers.set_source_status
    calls = {"n": 0}

    def crash_on_first_needs_setup(conn, source_id, status):
        if status == "needs_setup":
            calls["n"] += 1
            if calls["n"] == 1:
                raise _SimulatedCrash()
        return real_set_source_status(conn, source_id, status)

    monkeypatch.setattr(handlers, "set_source_status", crash_on_first_needs_setup)

    with pytest.raises(_SimulatedCrash):
        handle_ingest(library, payload)

    monkeypatch.setattr(handlers, "set_source_status", real_set_source_status)

    # reclaim_stale()'s retry: the same payload, inbox path now gone.
    handle_ingest(library, payload)

    sessions = list_sessions(conn)
    assert len(sessions) == 2
    session_b = next(s for s in sessions if s["id"] != session_a)
    sources_b = list_sources(conn, session_b["id"])
    assert len(sources_b) == 1
    assert sources_b[0]["status"] == "needs_setup"
    assert session_b["status"] == "needs_setup"

    # Session A must be completely untouched by session B's recovery.
    assert dict(get_source(conn, source_a_id)) == source_a_before


def test_ingest_raises_when_the_missing_file_matches_two_ingesting_sources(
    library, conn
):
    """Two sources stranded at 'ingesting' with the same original_name
    (two independent crashes racing each other) is genuinely ambiguous --
    the recovery lookup must not guess which one to finish. Both must be
    left exactly as they were rather than one being silently claimed.
    """
    session_a = create_session(conn, "session-a", "2024-01-01", "2024-01-01")
    session_b = create_session(conn, "session-b", "2024-01-02", "2024-01-02")

    source_a_id, idx_a = add_source(
        conn, session_a,
        recorded_at="2024-01-01T00:00:00+00:00",
        duration_ms=2000, width=640, height=360, fps=30.0,
        original_name="IMG_0001.mp4",
    )
    (library.source_dir(session_a, idx_a)).mkdir(parents=True)
    (library.source_dir(session_a, idx_a) / "original.mp4").write_bytes(b"a")

    source_b_id, idx_b = add_source(
        conn, session_b,
        recorded_at="2024-01-02T00:00:00+00:00",
        duration_ms=2000, width=640, height=360, fps=30.0,
        original_name="IMG_0001.mp4",
    )
    (library.source_dir(session_b, idx_b)).mkdir(parents=True)
    (library.source_dir(session_b, idx_b) / "original.mp4").write_bytes(b"b")

    ghost = library.inbox / "IMG_0001.mp4"

    with pytest.raises(FileNotFoundError) as excinfo:
        handle_ingest(library, {"path": str(ghost)})
    assert source_a_id in str(excinfo.value)
    assert source_b_id in str(excinfo.value)

    assert get_source(conn, source_a_id)["status"] == "ingesting"
    assert get_source(conn, source_b_id)["status"] == "ingesting"


def test_ingest_of_a_missing_inbox_file_with_no_matching_source_raises(library, conn):
    """A path that was never dropped into the inbox -- a typo'd payload, or
    a file deleted out from under a still-queued job -- must not be mistaken
    for "already ingested" just because src.exists() is False. Before this
    fix the early-return guard could not tell the two cases apart and
    returned as if the job had succeeded; it must now raise instead, so the
    job lands in 'failed' with a readable error rather than vanishing
    silently.
    """
    ghost = library.inbox / "IMG_9999.mp4"

    with pytest.raises(FileNotFoundError):
        handle_ingest(library, {"path": str(ghost)})

    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


# -- Task 5: ingest becomes register-only ------------------------------------

def test_ingest_registers_without_transcoding(library, sample_video, monkeypatch):
    called = []
    monkeypatch.setattr(
        "bootleg.jobs.handlers.make_proxy",
        lambda *a, **k: called.append(a),
    )
    src = library.inbox / "IMG_0001.MOV"
    src.write_bytes(sample_video.read_bytes())

    handle_ingest(library, {"path": str(src)})

    conn = connect(library.db_path)
    row = conn.execute("SELECT * FROM sources").fetchone()
    assert row["status"] == "needs_setup"
    assert called == []


def test_ingest_moves_the_original_into_the_session_tree(library, sample_video):
    src = library.inbox / "IMG_0002.MOV"
    src.write_bytes(sample_video.read_bytes())

    handle_ingest(library, {"path": str(src)})

    conn = connect(library.db_path)
    row = conn.execute("SELECT * FROM sources").fetchone()
    dest = library.source_dir(row["session_id"], row["idx"])
    assert not src.exists()
    assert (dest / "original.mov").is_file()


def test_ingest_enqueues_nothing(library, sample_video):
    src = library.inbox / "IMG_0003.MOV"
    src.write_bytes(sample_video.read_bytes())

    handle_ingest(library, {"path": str(src)})

    conn = connect(library.db_path)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_ingest_retry_after_the_original_moved_reaches_needs_setup_again(
    library, sample_video
):
    """Renamed from *_is_a_no_op: the retry is no longer a bare early
    return -- it now verifies the earlier move actually completed before
    reaffirming 'needs_setup', which is the write
    test_retry_after_a_crash_between_the_move_and_the_status_write_reaches_needs_setup
    shows can otherwise be lost to a crash.
    """
    src = library.inbox / "IMG_0004.MOV"
    src.write_bytes(sample_video.read_bytes())
    handle_ingest(library, {"path": str(src)})

    # The worker crashed and reclaim_stale requeued the same payload; the
    # inbox path is gone because the first attempt already moved it.
    handle_ingest(library, {"path": str(src)})

    conn = connect(library.db_path)
    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
    assert conn.execute("SELECT status FROM sources").fetchone()[0] == "needs_setup"


def test_ingest_recovery_does_not_rewind_an_advanced_source(
    library, conn, dropped_video
):
    """Regression test for the defect this fix addresses. When a requeued
    ingest job finds the source already moved with original.* on disk, the
    recovery path must not unconditionally rewind its status. A source that
    has been set up, built, and detected has advanced from 'ingesting' to
    'needs_setup' to 'building' to 'ingested' to 'detecting' to 'ready', and
    the recovery must not drag it back to 'needs_setup' -- that would drop
    a reviewed session out of the review UI and back into setup. Only sources
    still at 'ingesting' when recovered should be advanced to 'needs_setup';
    an already-advanced source has completed, and the requeue is redundant.
    """
    # First ingest: file goes from inbox to source dir, status becomes needs_setup.
    handle_ingest(library, {"path": str(dropped_video)})
    session = list_sessions(conn)[0]
    source = list_sources(conn, session["id"])[0]
    source_id = source["id"]
    session_id = session["id"]
    assert source["status"] == "needs_setup"
    assert session["status"] == "needs_setup"

    # Simulate the source being set up and detected: advance to ready.
    handlers.set_source_status(conn, source_id, "ready")
    handlers.set_session_status(conn, session_id, "ready")

    # Verify the state is ready before the recovery attempt.
    source_before = get_source(conn, source_id)
    assert source_before["status"] == "ready"
    session_before = conn.execute(
        "SELECT status FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    assert session_before["status"] == "ready"

    # Replay the same ingest payload: the inbox path is now gone (already
    # moved into source dir), so the recovery path fires. It should NOT
    # rewind the status just because the original.* file still exists.
    handle_ingest(library, {"path": str(dropped_video)})

    # Verify the source and session are STILL at ready, not rewound.
    source_after = get_source(conn, source_id)
    assert source_after["status"] == "ready"
    session_after = conn.execute(
        "SELECT status FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    assert session_after["status"] == "ready"


def test_ingest_stores_rotation_and_display_dimensions(library, tmp_path, sample_video):
    tagged = tmp_path / "tagged.mov"
    subprocess.run(
        ["ffmpeg", "-y", "-display_rotation", "90", "-i", str(sample_video),
         "-c", "copy", str(tagged)],
        check=True, capture_output=True,
    )
    src = library.inbox / "IMG_0005.MOV"
    src.write_bytes(tagged.read_bytes())

    handle_ingest(library, {"path": str(src)})

    conn = connect(library.db_path)
    row = conn.execute("SELECT * FROM sources").fetchone()
    assert row["rotation_deg"] in (90, 270)
    # 320x240 coded, quarter-turned for display.
    assert (row["width"], row["height"]) == (240, 320)


# -- Fix 4: replace the recovery lookup rule ---------------------------------

def test_ingest_recovery_of_a_completed_source_ignores_an_unrelated_failed_source(
    library, conn
):
    """Reproduces the fourth defect: an unscoped, name-only lookup with an
    arbitrary tiebreak (`ORDER BY idx LIMIT 1`) could resolve a recovery to
    a same-named source that failed before its move instead of the one that
    actually finished. Session F's source failed before `_move_to_failed`
    could even run, so its session dir holds no `original.*`; session R's
    same-named source ingested cleanly to 'needs_setup' with `original.*`
    on disk. Replaying R's already-finished payload must resolve by asking
    "does any source with this name hold a completed original" -- never by
    picking a row -- so it returns quietly and leaves both untouched.
    """
    session_f = create_session(conn, "session-f", "2024-01-01", "2024-01-01")
    source_f_id, _idx_f = add_source(
        conn, session_f,
        recorded_at="2024-01-01T00:00:00+00:00",
        duration_ms=2000, width=640, height=360, fps=30.0,
        original_name="IMG_0001.mp4",
    )
    handlers.set_source_status(conn, source_f_id, "failed")
    handlers.set_session_status(conn, session_f, "failed")
    # No mkdir here -- a source that failed before its move has nothing in
    # its session dir; _move_to_failed puts the file in `_inbox/failed/`.

    session_r = create_session(conn, "session-r", "2024-01-02", "2024-01-02")
    source_r_id, idx_r = add_source(
        conn, session_r,
        recorded_at="2024-01-02T00:00:00+00:00",
        duration_ms=2000, width=640, height=360, fps=30.0,
        original_name="IMG_0001.mp4",
    )
    handlers.set_source_status(conn, source_r_id, "needs_setup")
    handlers.set_session_status(conn, session_r, "needs_setup")
    r_dir = library.source_dir(session_r, idx_r)
    r_dir.mkdir(parents=True)
    (r_dir / "original.mp4").write_bytes(b"session r's original")

    source_f_before = dict(get_source(conn, source_f_id))
    source_r_before = dict(get_source(conn, source_r_id))

    # A redundant requeue of session R's already-finished job: same name,
    # inbox path already gone.
    ghost = library.inbox / "IMG_0001.mp4"
    handle_ingest(library, {"path": str(ghost)})

    assert dict(get_source(conn, source_f_id)) == source_f_before
    assert dict(get_source(conn, source_r_id)) == source_r_before


def test_ingest_raises_when_no_source_anywhere_holds_a_completed_original(
    library, conn
):
    """A payload naming a file that never existed anywhere must raise, even
    when a same-named source row exists -- as long as none of them ever
    completed a move. A failed source with an empty session dir must not be
    mistaken for "the earlier attempt finished".
    """
    session_f = create_session(conn, "session-f", "2024-01-01", "2024-01-01")
    source_f_id, _idx_f = add_source(
        conn, session_f,
        recorded_at="2024-01-01T00:00:00+00:00",
        duration_ms=2000, width=640, height=360, fps=30.0,
        original_name="IMG_9999.mp4",
    )
    handlers.set_source_status(conn, source_f_id, "failed")
    handlers.set_session_status(conn, session_f, "failed")

    ghost = library.inbox / "IMG_9999.mp4"

    with pytest.raises(FileNotFoundError):
        handle_ingest(library, {"path": str(ghost)})

    assert get_source(conn, source_f_id)["status"] == "failed"


def _registered(library, sample_video, name="IMG_1000.MOV"):
    src = library.inbox / name
    src.write_bytes(sample_video.read_bytes())
    handle_ingest(library, {"path": str(src)})
    conn = connect(library.db_path)
    return conn, conn.execute("SELECT * FROM sources").fetchone()


def test_build_proxy_passes_the_stored_rotation(library, sample_video, monkeypatch):
    conn, row = _registered(library, sample_video)
    set_source_rotation(conn, row["id"], 270)
    seen = {}
    monkeypatch.setattr(
        "bootleg.jobs.handlers.make_proxy",
        lambda src, dst, rotation_deg=0: seen.update(rotation_deg=rotation_deg) or dst.touch(),
    )
    monkeypatch.setattr("bootleg.jobs.handlers.make_thumbs", lambda *a, **k: None)
    monkeypatch.setattr(
        "bootleg.jobs.handlers.probe",
        lambda path: SimpleNamespace(width=100, height=200),
    )

    handle_build_proxy(library, {"source_id": row["id"]})

    assert seen["rotation_deg"] == 270


def test_build_proxy_records_the_proxys_actual_dimensions(library, sample_video):
    """Finding: add_source seeds width/height from the ORIGINAL's probed
    rotation at ingest time. set_source_setup (the wizard's write path)
    only ever writes rotation_deg and court_preset_id, and the old
    handle_build_proxy only wrote status -- so a source seeded at one
    rotation whose orientation the wizard later corrects keeps its stale
    ingest-time dimensions forever, even though the proxy on disk is a
    completely different shape. `bootleg doctor` prints exactly that pair
    (width/height vs rotation) as its headline diagnostic, so it lies on
    every source the wizard corrected.

    Reproduces the disagreement directly: 320x240 landscape ingests at
    rotation_deg=0 (sample_video carries no display-matrix tag), so the row
    seeds (320, 240). Correcting to a 90 rotation via set_source_rotation
    -- exactly what the wizard's setup call does -- changes ONLY
    rotation_deg, leaving width/height stale. A real build_proxy run (no
    make_proxy mock, so a real portrait-oriented proxy file lands on disk)
    must update the row to match what ffprobe reports for that actual
    file, not the stale ingest-time pair and not a value merely recomputed
    from rotation_deg without looking at the artifact.
    """
    conn, row = _registered(library, sample_video, name="IMG_1002.MOV")
    assert (row["width"], row["height"]) == (320, 240)
    set_source_rotation(conn, row["id"], 90)

    handle_build_proxy(library, {"source_id": row["id"]})

    src_dir = library.source_dir(row["session_id"], row["idx"])
    actual = probe(src_dir / "proxy.mp4")
    after = get_source(conn, row["id"])
    assert (after["width"], after["height"]) == (actual.width, actual.height)
    # The bug this guards against: silently keeping the stale ingest-time
    # pair despite the rotation (and therefore the proxy's shape) having
    # changed.
    assert (after["width"], after["height"]) != (320, 240)


def test_build_proxy_enqueues_detect(library, sample_video, monkeypatch):
    conn, row = _registered(library, sample_video, name="IMG_1001.MOV")
    monkeypatch.setattr(
        "bootleg.jobs.handlers.make_proxy", lambda src, dst, rotation_deg=0: dst.touch()
    )
    monkeypatch.setattr("bootleg.jobs.handlers.make_thumbs", lambda *a, **k: None)
    monkeypatch.setattr(
        "bootleg.jobs.handlers.probe",
        lambda path: SimpleNamespace(width=100, height=200),
    )

    handle_build_proxy(library, {"source_id": row["id"]})

    job = conn.execute("SELECT * FROM jobs WHERE type='detect'").fetchone()
    assert json.loads(job["payload"])["source_id"] == row["id"]
    assert conn.execute(
        "SELECT status FROM sources WHERE id=?", (row["id"],)
    ).fetchone()["status"] == "ingested"


def test_build_proxy_on_a_missing_source_raises(library):
    with pytest.raises(ValueError, match="No such source"):
        handle_build_proxy(library, {"source_id": "nope"})


# -- Defect: a source failure must not condemn a whole session -------------
# (see .superpowers/sdd/task-6-report.md, "one edge case worth flagging").
# refresh_session_review_status only ever acts on sessions already in
# ('ready', 'reviewed'); once a session is written 'failed' it can never
# transition again, so failing it while a sibling source already finished
# review would strand that sibling's reviewed rallies forever.

def test_build_proxy_failure_does_not_fail_the_session_when_a_sibling_is_ready(
    library, conn, sample_video, monkeypatch
):
    """Source A is already 'ready' (fully detected, possibly reviewed).
    Source B's build_proxy then fails; only B may be marked failed, and the
    session -- which A's finished work still lives under -- must not be
    dragged down with it.
    """
    _, row_a = _registered(library, sample_video, name="IMG_3000.MOV")
    session_id = row_a["session_id"]
    handlers.set_source_status(conn, row_a["id"], "ready")
    handlers.set_session_status(conn, session_id, "ready")

    source_b_id, idx_b = add_source(
        conn, session_id,
        recorded_at="2024-01-01T00:00:01+00:00",
        duration_ms=2000, width=640, height=360, fps=30.0,
        original_name="IMG_3001.MOV",
    )
    handlers.set_source_status(conn, source_b_id, "needs_setup")
    b_dir = library.source_dir(session_id, idx_b)
    b_dir.mkdir(parents=True)
    (b_dir / "original.mp4").write_bytes(sample_video.read_bytes())

    monkeypatch.setattr(
        "bootleg.jobs.handlers.make_proxy",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ffmpeg exploded")),
    )

    with pytest.raises(RuntimeError):
        handle_build_proxy(library, {"source_id": source_b_id})

    assert get_source(conn, source_b_id)["status"] == "failed"
    session_after = conn.execute(
        "SELECT status FROM sessions WHERE id=?", (session_id,)
    ).fetchone()
    assert session_after["status"] != "failed"
    assert session_after["status"] == "ready"


def test_build_proxy_failure_fails_the_session_when_it_is_the_only_source(
    library, conn, sample_video, monkeypatch
):
    """Existing behaviour, unchanged: a session with only one source still
    fails wholesale when that source's build_proxy fails -- there is no
    sibling left to protect.
    """
    _, row = _registered(library, sample_video, name="IMG_3002.MOV")
    monkeypatch.setattr(
        "bootleg.jobs.handlers.make_proxy",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ffmpeg exploded")),
    )

    with pytest.raises(RuntimeError):
        handle_build_proxy(library, {"source_id": row["id"]})

    assert get_source(conn, row["id"])["status"] == "failed"
    session_after = conn.execute(
        "SELECT status FROM sessions WHERE id=?", (row["session_id"],)
    ).fetchone()
    assert session_after["status"] == "failed"


def test_ingest_failure_does_not_fail_the_session_when_a_sibling_is_ready(
    library, conn, dropped_video, monkeypatch
):
    """Same healthy-sibling rule as build_proxy (see
    test_build_proxy_failure_does_not_fail_the_session_when_a_sibling_is_ready):
    a second file dropped the same day joins session A's session (see
    test_second_file_same_day_joins_the_same_session); a failure registering
    it must not fail the session out from under A's already-reviewed work.
    """
    handle_ingest(library, {"path": str(dropped_video)})
    session = list_sessions(conn)[0]
    source_a = list_sources(conn, session["id"])[0]
    handlers.set_source_status(conn, source_a["id"], "ready")
    handlers.set_session_status(conn, session["id"], "ready")

    second = library.inbox / "IMG_0002.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=640x360:rate=30:duration=2",
         "-c:v", "libx264", str(second)],
        check=True, capture_output=True,
    )

    real_move = handlers.shutil.move
    calls = {"n": 0}

    def flaky_move(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk exploded")
        return real_move(src, dst, *a, **kw)

    monkeypatch.setattr(handlers.shutil, "move", flaky_move)

    with pytest.raises(OSError):
        handle_ingest(library, {"path": str(second)})

    sources = list_sources(conn, session["id"])
    assert len(sources) == 2
    source_b = sources[1]
    assert source_b["status"] == "failed"
    session_after = conn.execute(
        "SELECT status FROM sessions WHERE id=?", (session["id"],)
    ).fetchone()
    assert session_after["status"] == "ready"


# -- Defect: a duplicate detect job can discard hand-edited rally bounds ---

def test_build_proxy_retried_after_the_first_run_does_not_duplicate_detect(
    library, sample_video, monkeypatch
):
    """Simulates the crash window described in task-6-report.md: a worker
    dies after handle_build_proxy's jobq.enqueue('detect', ...) call but
    before the build_proxy job row itself is marked done, so
    reclaim_stale() requeues build_proxy and this handler runs a second
    time for the same source. handle_detect's replace_rallies discards
    manual rally-boundary edits by design (only starred/rejected survive by
    overlap), so a second 'detect' job must never be queued while the first
    is still pending.
    """
    conn, row = _registered(library, sample_video, name="IMG_3003.MOV")
    monkeypatch.setattr(
        "bootleg.jobs.handlers.make_proxy", lambda src, dst, rotation_deg=0: dst.touch()
    )
    monkeypatch.setattr("bootleg.jobs.handlers.make_thumbs", lambda *a, **k: None)
    monkeypatch.setattr(
        "bootleg.jobs.handlers.probe",
        lambda path: SimpleNamespace(width=100, height=200),
    )

    handle_build_proxy(library, {"source_id": row["id"]})
    handle_build_proxy(library, {"source_id": row["id"]})

    jobs = conn.execute("SELECT * FROM jobs WHERE type='detect'").fetchall()
    assert len(jobs) == 1
