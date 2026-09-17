import subprocess

import pytest

from splitstep.config import NotEnoughSpace
from splitstep.db.rallies import list_rallies, replace_rallies
from splitstep.db.reels import add_items, create_reel, get_reel
from splitstep.db.sessions import add_source, find_or_create_session_for_date
from splitstep.db.settings import set_hr_clips_root
from splitstep.detect.segment import Interval
from splitstep.jobs.handlers import HANDLERS, handle_reel
from splitstep.media.clips import clip_relpath
from splitstep.media.probe import probe
from splitstep.media.transcode import TranscodeError


def _real_clip(path, seconds=1.0):
    """A clip that shares one profile with its siblings, so -c copy applies.

    320x240 rather than the locked 3840x2160: this exercises the handler's
    wiring, and three 4K encodes per test would make the suite unusable. The
    profile parameters that -c copy actually cares about (codec, pix_fmt,
    rate, audio layout) still match across the inputs, which is the real
    precondition.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
         "-r", "30", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
         "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


@pytest.fixture
def reel_of_two(library, conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.6)])
    reel = create_reel(conn, "2026-08-18 points")
    spans = [(source_id, 1000, 5000), (source_id, 9000, 14000)]
    add_items(conn, reel["id"], spans)
    return {
        "reel": reel, "session_id": session_id, "source_id": source_id,
        "idx": idx, "spans": spans,
    }


def _cut_all(library, fx, seconds=1.0):
    clips = library.clips_dir(fx["session_id"])
    return [
        _real_clip(clips / clip_relpath(fx["idx"], start, end), seconds)
        for _src, start, end in fx["spans"]
    ]


def test_reel_is_registered(library):
    assert HANDLERS["reel"] is handle_reel


def test_handle_reel_writes_the_slug_named_file(library, conn, reel_of_two):
    parts = _cut_all(library, reel_of_two)

    handle_reel(library, {"reel_id": reel_of_two["reel"]["id"]})

    dst = library.reels_dir / "2026-08-18-points.mp4"
    assert dst.exists()
    total = sum(probe(p).duration_ms for p in parts)
    assert abs(probe(dst).duration_ms - total) <= 500


def test_handle_reel_records_a_library_relative_path_and_clears_dirty(
    library, conn, reel_of_two
):
    _cut_all(library, reel_of_two)
    handle_reel(library, {"reel_id": reel_of_two["reel"]["id"]})

    row = get_reel(conn, reel_of_two["reel"]["id"])
    # Library-relative, never absolute: the drive mounts at a different point
    # on each machine, same rule rallies.clip_path follows.
    assert row["rendered_path"] == "reels/2026-08-18-points.mp4"
    assert not row["rendered_path"].startswith("/")
    assert (library.root / row["rendered_path"]).exists()
    assert row["dirty"] == 0
    assert row["rendered_at"] is not None


def test_handle_reel_refuses_while_a_clip_is_missing(library, conn, reel_of_two):
    # Defence in depth behind the route's own 409: a clip can be deleted
    # between enqueue and run, and a reel silently short one point is exactly
    # the failure the duration probe exists to catch. Naming the count is
    # what makes the jobs badge's error readable.
    fx = reel_of_two
    clips = library.clips_dir(fx["session_id"])
    _real_clip(clips / clip_relpath(fx["idx"], 1000, 5000))

    with pytest.raises(ValueError, match="1 clip"):
        handle_reel(library, {"reel_id": fx["reel"]["id"]})

    assert not (library.reels_dir / "2026-08-18-points.mp4").exists()
    assert get_reel(conn, fx["reel"]["id"])["dirty"] == 1


def test_handle_reel_refuses_an_empty_reel(library, conn):
    reel = create_reel(conn, "empty")
    with pytest.raises(ValueError, match="no items"):
        handle_reel(library, {"reel_id": reel["id"]})


def test_handle_reel_refuses_an_unknown_reel(library):
    with pytest.raises(ValueError, match="No such reel"):
        handle_reel(library, {"reel_id": "nope"})


def test_handle_reel_is_idempotent(library, conn, reel_of_two):
    _cut_all(library, reel_of_two)
    handle_reel(library, {"reel_id": reel_of_two["reel"]["id"]})
    first = (library.reels_dir / "2026-08-18-points.mp4").stat().st_size
    handle_reel(library, {"reel_id": reel_of_two["reel"]["id"]})
    assert (library.reels_dir / "2026-08-18-points.mp4").stat().st_size == first


def test_handle_reel_checks_free_space_first(library, conn, reel_of_two, monkeypatch):
    _cut_all(library, reel_of_two)
    monkeypatch.setattr(type(library), "free_bytes", lambda self: 1)
    with pytest.raises(NotEnoughSpace):
        handle_reel(library, {"reel_id": reel_of_two["reel"]["id"]})
    assert not (library.reels_dir / "2026-08-18-points.mp4").exists()


def test_handle_reel_numbered_burns_a_counter_and_note_and_cleans_up(
    library, conn, reel_of_two
):
    fx = reel_of_two
    # One item gets a note, the other is left blank -- the empty-notes case
    # is covered separately below, but this exercises both the counter+note
    # and counter-only overlay in the same render.
    conn.execute(
        "UPDATE reel_items SET note = ? WHERE source_id = ? AND start_ms = ? AND end_ms = ?",
        ("match point", fx["source_id"], 1000, 5000),
    )
    conn.commit()
    parts = _cut_all(library, fx)

    handle_reel(library, {"reel_id": fx["reel"]["id"], "numbered": True})

    dst = library.reels_dir / "2026-08-18-points.mp4"
    assert dst.exists()
    total = sum(probe(p).duration_ms for p in parts)
    assert abs(probe(dst).duration_ms - total) <= 500

    # The dot-prefixed intermediate dir is cleaned up regardless of outcome --
    # a stray one would sit forever in reels/ and confuse a plain listing.
    leftovers = [p for p in library.reels_dir.iterdir() if ".numbered." in p.name]
    assert leftovers == []

    row = get_reel(conn, fx["reel"]["id"])
    assert row["rendered_path"] == "reels/2026-08-18-points.mp4"
    assert row["dirty"] == 0


def test_handle_reel_numbered_with_no_notes_still_renders(library, conn, reel_of_two):
    # Every item's note is "" by default (see add_items) -- render_overlay_png
    # falls back to counter-only, and the render must still succeed.
    fx = reel_of_two
    parts = _cut_all(library, fx)

    handle_reel(library, {"reel_id": fx["reel"]["id"], "numbered": True})

    dst = library.reels_dir / "2026-08-18-points.mp4"
    assert dst.exists()
    total = sum(probe(p).duration_ms for p in parts)
    assert abs(probe(dst).duration_ms - total) <= 500


def test_handle_reel_numbered_cleans_up_the_temp_dir_on_a_failed_encode(
    library, conn, reel_of_two, monkeypatch
):
    """The cleanup must run even when an intermediate's own encode raises --
    `shutil.rmtree(tmp_dir, ignore_errors=True)` sits in a `finally`
    specifically so a real overlay-encode failure (a corrupt clip, a full
    disk mid-encode) never leaves a stray `.<slug>.numbered.<hex>/`
    directory behind in reels/, and so that the encode's own exception is
    what propagates rather than something from cleanup. Forced via
    monkeypatch so this holds independent of any real ffmpeg failure mode.
    """
    import splitstep.jobs.handlers as handlers_mod

    fx = reel_of_two
    _cut_all(library, fx)

    def _boom(*_a, **_k):
        raise TranscodeError("synthetic failure for the cleanup test")

    monkeypatch.setattr(handlers_mod, "make_numbered_intermediate", _boom)

    with pytest.raises(TranscodeError, match="synthetic failure"):
        handle_reel(library, {"reel_id": fx["reel"]["id"], "numbered": True})

    assert not (library.reels_dir / "2026-08-18-points.mp4").exists()
    assert list(library.reels_dir.iterdir()) == []
    assert get_reel(conn, fx["reel"]["id"])["dirty"] == 1


def test_a_reencode_fallback_still_marks_the_reel_rendered(
    library, conn, reel_of_two, monkeypatch, caplog
):
    # The fallback is a slower success, not a failure: the reel is rendered
    # and dirty is cleared. It is logged so a silent -c copy problem leaves a
    # trace rather than only a slower render nobody notices.
    import splitstep.media.concat as concat_mod

    _cut_all(library, reel_of_two)
    real_run = concat_mod.run_ffmpeg
    seen = []

    def fake_run(args, timeout=None, on_progress=None, total_ms=None):
        seen.append(args)
        if "copy" in args and len(seen) == 1:
            real_run(["-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=0.2",
                      "-f", "lavfi", "-i", "sine=frequency=440:duration=0.2",
                      "-c:v", "libx264", "-pix_fmt", "yuv420p",
                      "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", args[-1]])
            return
        real_run(args)

    monkeypatch.setattr(concat_mod, "run_ffmpeg", fake_run)

    with caplog.at_level("WARNING"):
        handle_reel(library, {"reel_id": reel_of_two["reel"]["id"]})

    assert get_reel(conn, reel_of_two["reel"]["id"])["dirty"] == 0
    # concat_clips logs its own "re-encoding" message, so a loose substring
    # match cannot tell the handler's reel-naming line from concat's diagnostic.
    # This assertion pins the handler's log entry specifically by checking for
    # the slug combined with the "fell back" phrase unique to handle_reel.
    slug = reel_of_two["reel"]["slug"]
    assert any((slug in r.message and "fell back" in r.message) or
               (slug in r.getMessage() and "fell back" in r.getMessage())
               for r in caplog.records)


def _hr_copies(library, fx, hr_root, seconds=2.0):
    """RallyMetrics-shaped copies: same relpath under the SplitStep session id,
    longer than the originals so a render that used them is measurable."""
    return [
        _real_clip(hr_root / fx["session_id"] / clip_relpath(fx["idx"], start, end), seconds)
        for _src, start, end in fx["spans"]
    ]


def test_handle_reel_hr_uses_the_overlaid_copies(library, conn, reel_of_two, tmp_path):
    fx = reel_of_two
    _cut_all(library, fx, seconds=1.0)
    hr_root = tmp_path / "rm" / "clips"
    hr_parts = _hr_copies(library, fx, hr_root, seconds=2.0)
    set_hr_clips_root(conn, hr_root)

    handle_reel(library, {"reel_id": fx["reel"]["id"], "hr": True})

    dst = library.reels_dir / "2026-08-18-points.mp4"
    total = sum(probe(p).duration_ms for p in hr_parts)
    assert abs(probe(dst).duration_ms - total) <= 500  # ~4 s, not the originals' ~2 s


def test_handle_reel_hr_refuses_when_one_copy_is_missing(library, conn, reel_of_two, tmp_path):
    fx = reel_of_two
    _cut_all(library, fx)
    hr_root = tmp_path / "rm" / "clips"
    set_hr_clips_root(conn, hr_root)
    _src, start, end = fx["spans"][0]
    _real_clip(hr_root / fx["session_id"] / clip_relpath(fx["idx"], start, end), 1.0)

    with pytest.raises(ValueError, match="1 clip\\(s\\) with no heart-rate overlay"):
        handle_reel(library, {"reel_id": fx["reel"]["id"], "hr": True})
    assert not (library.reels_dir / "2026-08-18-points.mp4").exists()


def test_handle_reel_hr_refuses_without_a_configured_root(library, conn, reel_of_two):
    fx = reel_of_two
    _cut_all(library, fx)
    with pytest.raises(ValueError, match="set-hr-clips"):
        handle_reel(library, {"reel_id": fx["reel"]["id"], "hr": True})


def test_handle_reel_numbered_burns_the_score_entering_each_clip(
    library, conn, reel_of_two, monkeypatch
):
    from splitstep.db.rallies import set_winner
    from splitstep.db.sessions import set_scoring
    from splitstep.jobs import handlers as handlers_mod

    fx = reel_of_two
    set_scoring(conn, fx["session_id"], {
        "players": ["Me", "Opp"], "sets": 3, "ad": True, "tiebreak": "at6", "tiebreakTo": 7,
    })
    rows = list_rallies(conn, fx["session_id"])
    set_winner(conn, rows[0]["id"], "a")
    set_winner(conn, rows[1]["id"], "b")
    _cut_all(library, fx)

    seen: list[list[list[str]] | None] = []

    def spy(dst, *, counter, note, font, scoreboard=None):
        seen.append(scoreboard)
        return real(dst, counter=counter, note=note, font=font, scoreboard=scoreboard)

    real = handlers_mod.render_overlay_png
    monkeypatch.setattr(handlers_mod, "render_overlay_png", spy)

    handle_reel(library, {"reel_id": fx["reel"]["id"], "numbered": True})

    # Clip 1 is the first point: nothing has been scored yet. Clip 2 enters
    # at 15-0 to Me -- the score BEFORE the point it contains.
    assert seen == [
        [["Me", "0", "0"], ["Opp", "0", "0"]],
        [["Me", "0", "15"], ["Opp", "0", "0"]],
    ]


def test_handle_reel_numbered_has_no_board_for_an_untracked_session(
    library, conn, reel_of_two, monkeypatch
):
    from splitstep.jobs import handlers as handlers_mod

    fx = reel_of_two
    _cut_all(library, fx)
    seen = []
    real = handlers_mod.render_overlay_png

    def spy(dst, *, counter, note, font, scoreboard=None):
        seen.append(scoreboard)
        return real(dst, counter=counter, note=note, font=font, scoreboard=scoreboard)

    monkeypatch.setattr(handlers_mod, "render_overlay_png", spy)
    handle_reel(library, {"reel_id": fx["reel"]["id"], "numbered": True})
    assert seen == [None, None]
