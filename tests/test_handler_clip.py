import subprocess

import pytest

from splitstep.config import Library, NotEnoughSpace
from splitstep.db.jobs import enqueue, has_pending_clip
from splitstep.db.rallies import replace_rallies
from splitstep.db.settings import get_color_profile
from splitstep.detect.segment import Interval
from splitstep.jobs.handlers import handle_clip, handle_ingest
from splitstep.media.clips import clip_relpath
from splitstep.media.probe import probe
from splitstep.media.transcode import CLIP_HEIGHT, CLIP_WIDTH


@pytest.fixture
def a_rally(library, conn, registered_source):
    """One rally over a source whose original is on disk.

    registered_source's sample video is 2 s long, so the span stays inside it
    -- a cut past the end would produce a short clip and mask a real failure.
    """
    replace_rallies(conn, registered_source.session_id, registered_source.id,
                    [Interval(200, 1200, 0.8)])
    rally = conn.execute("SELECT * FROM rallies").fetchone()
    return {
        "payload": {
            "source_id": registered_source.id,
            "rally_id": rally["id"],
            "start_ms": 200,
            "end_ms": 1200,
        },
        "source": registered_source,
        "rally_id": rally["id"],
    }


def _clip_path(library, source, start_ms, end_ms):
    return library.clips_dir(source.session_id) / clip_relpath(source.idx, start_ms, end_ms)


def test_handle_clip_writes_a_span_named_file(library, conn, a_rally):
    handle_clip(library, a_rally["payload"])
    dst = _clip_path(library, a_rally["source"], 200, 1200)
    assert dst.exists()
    info = probe(dst)
    assert (info.width, info.height) == (CLIP_WIDTH, CLIP_HEIGHT)


def test_handle_clip_records_a_library_relative_clip_path(library, conn, a_rally):
    handle_clip(library, a_rally["payload"])
    stored = conn.execute(
        "SELECT clip_path FROM rallies WHERE id = ?", (a_rally["rally_id"],)
    ).fetchone()["clip_path"]
    # Library-relative, never absolute: the path is read on whatever machine
    # has the drive mounted, and that mountpoint differs between them.
    assert not stored.startswith("/")
    assert (library.root / stored).exists()


def test_handle_clip_is_idempotent(library, conn, a_rally):
    # reclaim_stale() can requeue a handler that already ran, so a second run
    # must overwrite rather than fail. A clip half-written by a killed worker
    # is worthless; re-encoding is the only safe retry.
    handle_clip(library, a_rally["payload"])
    handle_clip(library, a_rally["payload"])
    assert _clip_path(library, a_rally["source"], 200, 1200).exists()


def test_handle_clip_raises_on_an_unknown_source(library, conn, a_rally):
    payload = {**a_rally["payload"], "source_id": "nope"}
    with pytest.raises(ValueError, match="No such source"):
        handle_clip(library, payload)


def test_handle_clip_cuts_from_the_proxy_when_the_original_is_reclaimed(
    library, conn, a_rally, hlg_setparams
):
    source = a_rally["source"]
    # Stand in for a reclaimed source: proxy present, original flag cleared.
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-vf", hlg_setparams,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(source.dir / "proxy.mp4")],
        check=True, capture_output=True,
    )
    conn.execute("UPDATE sources SET has_original = 0 WHERE id = ?", (source.id,))
    conn.commit()

    handle_clip(library, a_rally["payload"])
    info = probe(_clip_path(library, source, 200, 1200))
    # Conformed to the locked frame even from 1080p. Nothing marks the clip
    # as upscaled -- see handle_clip on why that claim was removed rather
    # than made true -- but it must stay concat-compatible, which is the
    # property that cannot be compromised.
    assert (info.width, info.height) == (CLIP_WIDTH, CLIP_HEIGHT)


def test_handle_clip_refuses_before_encoding_when_space_is_short(
    library, conn, a_rally, monkeypatch
):
    def no_space(_self, _need):
        raise NotEnoughSpace("nope")

    # Library is a frozen dataclass, so its require_free cannot be
    # monkeypatched on the instance (that raises FrozenInstanceError) --
    # patch it on the class instead.
    monkeypatch.setattr(Library, "require_free", no_space)
    with pytest.raises(NotEnoughSpace):
        handle_clip(library, a_rally["payload"])
    # Refused before writing, not partway through: a 4K clip that dies at 90%
    # is worse than a job that never started.
    assert not _clip_path(library, a_rally["source"], 200, 1200).exists()


def test_has_pending_clip_distinguishes_two_spans_of_one_source(conn, a_rally):
    """The load-bearing case.

    has_pending_job matches on source_id alone, which is right for
    ingest/build_proxy/detect (one per source) and wrong for clips: one source
    yields dozens, so a source-wide check would let the first enqueued clip
    suppress every other clip from the same session.
    """
    source_id = a_rally["payload"]["source_id"]
    enqueue(conn, "clip", {"source_id": source_id, "rally_id": "r1",
                           "start_ms": 200, "end_ms": 1200})

    assert has_pending_clip(conn, source_id, 200, 1200) is True
    assert has_pending_clip(conn, source_id, 9000, 14000) is False


def test_handle_clip_reports_progress_through_the_callback_it_is_given(
    library, conn, a_rally
):
    """`jobs.progress` has existed since 001_init.sql and nothing has ever
    written it -- so the only feedback during a half-hour export was the
    badge's "N jobs running", true from the first second to the last. The
    handler takes the reporter rather than a job id so it never has to know
    the queue exists.
    """
    seen: list[float] = []
    handle_clip(library, a_rally["payload"], seen.append)
    assert seen, "no progress reported"
    assert seen[-1] == 1.0


def test_handle_clip_still_runs_without_a_progress_callback(library, conn, a_rally):
    # The CLI and the tests call handlers directly, with no queue behind
    # them and nothing to report to.
    handle_clip(library, a_rally["payload"])
    assert _clip_path(library, a_rally["source"], 200, 1200).exists()


def test_handle_clip_without_a_rally_id_still_cuts(library, conn, a_rally):
    # A reel item whose rally vanished under a re-segment has no rally_id to
    # offer, and it must still be cuttable -- see plan_reel_export. The clip
    # is written; there is simply no row to record clip_path on.
    payload = {k: v for k, v in a_rally["payload"].items() if k != "rally_id"}
    handle_clip(library, payload)
    assert _clip_path(library, a_rally["source"], 200, 1200).exists()


def test_first_export_locks_a_non_hlg_profile_end_to_end(library, conn, tmp_path):
    """A bt709 source in a fresh library locks bt709 and encodes bt709 output.

    The per-library colour profile shipped with only default-profile (HLG)
    coverage of the encode path; this is the end-to-end test at the *other*
    profile the Phase-1 review asked for before Phase 3 leans on it. bt709
    throughout is what a phone with HDR off records -- the exact friend
    scenario the per-library lock exists for.

    setparams as a filter, not -color_* output flags, for the same measured
    reason as the hlg_setparams fixture: with a lavfi input the flags
    silently drop primaries and transfer.
    """
    sdr_params = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
    sample = tmp_path / "sdr.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-vf", sdr_params,
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(sample)],
        check=True, capture_output=True,
    )
    dropped = library.inbox / "IMG_8000.MOV"
    dropped.write_bytes(sample.read_bytes())
    handle_ingest(library, {"path": str(dropped)})
    row = conn.execute("SELECT * FROM sources").fetchone()
    replace_rallies(conn, row["session_id"], row["id"], [Interval(200, 1200, 0.8)])

    handle_clip(library, {"source_id": row["id"], "start_ms": 200, "end_ms": 1200})

    sdr = ("tv", "bt709", "bt709", "bt709")
    assert get_color_profile(conn) == sdr
    dst = library.clips_dir(row["session_id"]) / clip_relpath(row["idx"], 200, 1200)
    info = probe(dst)
    assert (info.color_range, info.color_space, info.color_transfer,
            info.color_primaries) == sdr
