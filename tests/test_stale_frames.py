"""The sweep that closes the portrait-clip migration.

Before `transcode.clip_frame`, every clip was conformed to the landscape
frame, so a portrait source's clips are 3840x2160 with the picture
pillarboxed into the middle. Those clips are not orphans -- a rally still
claims every one of their spans -- and they are not broken either. What they
are is unmixable with anything cut after the change, so they have to be
found by shape rather than by name, which is the one question the rest of
this app never asks about a clip.
"""

import subprocess

import pytest

from splitstep.db.rallies import list_rallies, replace_rallies, set_clip_path
from splitstep.db.sessions import add_source, find_or_create_session_for_date
from splitstep.detect.segment import Interval
from splitstep.export import delete_stale_frame_clips, find_stale_frame_clips
from splitstep.media.clips import clip_relpath

SPANS = [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7)]


def _encode(path, size):
    """A real (tiny) video at `size`, because this sweep probes the file.

    Small on purpose: the sweep reads frame dimensions, and nothing about
    what it decides depends on the clip being 4K.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration=0.2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


@pytest.fixture
def portrait(library, conn):
    """A source the way the library's real ones look: `sources.width/height`
    is the PROXY's frame, so a portrait source is 608x1080 on the row."""
    session_id = find_or_create_session_for_date(conn, "2026-08-28")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-28T10:00:00Z", duration_ms=600_000,
        width=608, height=1080, fps=30.0, original_name="IMG_9100.MOV",
    )
    replace_rallies(conn, session_id, source_id, SPANS)
    return {"session_id": session_id, "source_id": source_id, "idx": idx}


@pytest.fixture
def landscape(library, conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=1920, height=1080, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id, SPANS)
    return {"session_id": session_id, "source_id": source_id, "idx": idx}


def _clip_at(library, fx, interval, size):
    return _encode(
        library.clips_dir(fx["session_id"])
        / clip_relpath(fx["idx"], interval.start_ms, interval.end_ms),
        size,
    )


def test_a_pillarboxed_clip_from_a_portrait_source_is_stale(library, conn, portrait):
    _clip_at(library, portrait, SPANS[0], "3840x2160")

    stale = find_stale_frame_clips(library, conn, portrait["session_id"])

    assert len(stale) == 1
    assert stale[0].frame == (3840, 2160)
    assert stale[0].expected == (2160, 3840)
    assert stale[0].source_idx == portrait["idx"]
    assert (stale[0].start_ms, stale[0].end_ms) == (SPANS[0].start_ms, SPANS[0].end_ms)


def test_a_clip_at_its_sources_own_frame_is_not_stale(library, conn, portrait):
    _clip_at(library, portrait, SPANS[0], "2160x3840")
    assert find_stale_frame_clips(library, conn, portrait["session_id"]) == []


def test_a_landscape_sources_clips_are_left_alone(library, conn, landscape):
    """The sweep must not turn into a re-cut of the whole library: a
    landscape source's clips were already the right shape and stay it."""
    for interval in SPANS:
        _clip_at(library, landscape, interval, "3840x2160")
    assert find_stale_frame_clips(library, conn, landscape["session_id"]) == []


def test_a_file_this_library_did_not_name_is_left_alone(library, conn, portrait):
    """Same gate the orphan sweep uses -- `parse_clip_name` admits exactly
    the names this library writes, so someone else's file in clips/ is never
    swept up, whatever shape it is."""
    clips = library.clips_dir(portrait["session_id"])
    _encode(clips / f"{portrait['idx']:02d}" / "notes.mp4", "3840x2160")
    assert find_stale_frame_clips(library, conn, portrait["session_id"]) == []


def test_a_clip_that_cannot_be_probed_is_left_alone(library, conn, portrait):
    """This list is handed to unlink(). "I could not read it" is not
    evidence that a clip is the wrong shape -- a spun-down drive would
    otherwise read as the whole session being stale."""
    path = (library.clips_dir(portrait["session_id"])
            / clip_relpath(portrait["idx"], SPANS[0].start_ms, SPANS[0].end_ms))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a video")

    assert find_stale_frame_clips(library, conn, portrait["session_id"]) == []
    assert path.exists()


def test_a_session_that_never_cut_a_clip_has_none(library, conn, portrait):
    assert find_stale_frame_clips(library, conn, portrait["session_id"]) == []


def test_deleting_stale_clips_removes_the_files_and_clears_clip_path(
    library, conn, portrait
):
    """`clip_path` records what WAS cut, not what the current bounds imply,
    so a row naming a file that is about to go has to be cleared -- the same
    dangling reference delete_orphan_clips exists to prevent."""
    stale_path = _clip_at(library, portrait, SPANS[0], "3840x2160")
    kept = _clip_at(library, portrait, SPANS[1], "2160x3840")
    rally = list_rallies(conn, portrait["session_id"])[0]
    set_clip_path(conn, rally["id"], str(stale_path.relative_to(library.root)))

    stale = find_stale_frame_clips(library, conn, portrait["session_id"])
    assert delete_stale_frame_clips(library, conn, stale) == 1

    assert not stale_path.exists()
    assert kept.exists()
    assert conn.execute(
        "SELECT clip_path FROM rallies WHERE id = ?", (rally["id"],)
    ).fetchone()["clip_path"] is None


def test_deleting_a_clip_that_already_went_is_not_an_error(library, conn, portrait):
    """Someone in Finder got there first. Not a failure, and not a reason to
    skip the clip_path clear."""
    path = _clip_at(library, portrait, SPANS[0], "3840x2160")
    stale = find_stale_frame_clips(library, conn, portrait["session_id"])
    path.unlink()

    assert delete_stale_frame_clips(library, conn, stale) == 0
