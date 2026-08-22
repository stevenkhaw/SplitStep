import pytest

from bootleg.db.rallies import replace_rallies, set_rejected
from bootleg.db.reels import add_items, create_reel
from bootleg.db.sessions import add_source, find_or_create_session_for_date
from bootleg.detect.segment import Interval
from bootleg.export import delete_orphan_clips, find_orphan_clips
from bootleg.media.clips import clip_relpath

SPANS = [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7), Interval(20000, 26000, 0.6)]


@pytest.fixture
def seeded(library, conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id, SPANS)
    return {"session_id": session_id, "source_id": source_id, "idx": idx}


def _cut(library, session_id, name, payload=b"pretend this is 40 MB of H.264"):
    """Stand in for a finished encode: a file at a name in clips/."""
    path = library.clips_dir(session_id) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_a_clip_matching_a_current_span_is_not_an_orphan(library, conn, seeded):
    for iv in SPANS:
        _cut(library, seeded["session_id"], clip_relpath(seeded["idx"], iv.start_ms, iv.end_ms))
    assert find_orphan_clips(library, conn, seeded["session_id"]) == []


def test_a_clip_left_behind_by_a_re_segment_is_an_orphan(library, conn, seeded):
    """The whole reason this exists. A threshold sweep moves a span, the
    rally's bounds resolve to a different filename, and the file cut at the
    old bounds is stranded: nothing enumerates it, nothing removes it, and
    at roughly 2 MB per second of clip a few sweeps is real dead weight.
    """
    session_id, idx = seeded["session_id"], seeded["idx"]
    for iv in SPANS:
        _cut(library, session_id, clip_relpath(idx, iv.start_ms, iv.end_ms))

    moved = [Interval(1000, 5000, 0.8), Interval(9200, 13800, 0.7), Interval(20000, 26000, 0.6)]
    replace_rallies(conn, session_id, seeded["source_id"], moved)

    orphans = find_orphan_clips(library, conn, session_id)
    assert [(o.start_ms, o.end_ms) for o in orphans] == [(9000, 14000)]
    assert orphans[0].source_idx == idx
    assert orphans[0].path.name == clip_relpath(idx, 9000, 14000)
    assert orphans[0].size_bytes == orphans[0].path.stat().st_size


def test_a_clip_a_reel_holds_survives_a_resegment(library, conn, seeded):
    """reels.py's own invariant, from ReelItem's docstring: an item whose
    rally has vanished under a re-segment "renders as orphaned and stays
    playable, cuttable and renderable -- the clip on disk is what the reel
    is made of". find_orphan_clips must honour that claim, not just the
    rallies table's -- otherwise `clips prune` deletes footage a reel is
    still built from the moment a threshold sweep moves the rally out from
    under it, at ~2 MB/second and with no re-encode possible if the
    original has since been discarded.
    """
    session_id, source_id, idx = seeded["session_id"], seeded["source_id"], seeded["idx"]
    reel = create_reel(conn, "highlights")
    add_items(conn, reel["id"], [(source_id, 9000, 14000)])
    _cut(library, session_id, clip_relpath(idx, 9000, 14000))

    # The threshold sweep that carries the star/rejected flags across but
    # moves this span just enough that the rally no longer names this file.
    moved = [Interval(1000, 5000, 0.8), Interval(9200, 13800, 0.7), Interval(20000, 26000, 0.6)]
    replace_rallies(conn, session_id, source_id, moved)

    orphans = find_orphan_clips(library, conn, session_id)
    assert clip_relpath(idx, 9000, 14000) not in {o.path.name for o in orphans}


def test_a_rejected_rallys_clip_is_not_an_orphan(library, conn, seeded):
    """Orphaned is a question about the span, not about the verdict. A
    reviewer who rejects a rally after its clip was cut has not stranded the
    file -- a rally still holds those bounds, and un-rejecting is one
    keystroke away."""
    session_id, idx = seeded["session_id"], seeded["idx"]
    _cut(library, session_id, clip_relpath(idx, 1000, 5000))
    rally = conn.execute("SELECT id FROM rallies ORDER BY idx").fetchone()
    set_rejected(conn, rally["id"], True)
    assert find_orphan_clips(library, conn, session_id) == []


def test_an_encode_in_flight_is_never_an_orphan(library, conn, seeded):
    """make_clip's temp file is a dot-prefixed .part sibling living in this
    very directory while ffmpeg writes it. Sweeping one up would delete a
    running encode's output partway through."""
    session_id = seeded["session_id"]
    _cut(library, session_id, ".01-9000-14000.deadbeef.part.mp4")
    assert find_orphan_clips(library, conn, session_id) == []


def test_a_file_this_library_did_not_name_is_left_alone(library, conn, seeded):
    """clips/ is a folder on the user's own drive. A file whose name is not
    exactly a clip_relpath name is not a clip we cut, and the sweep must not
    claim -- or delete -- something it does not recognise."""
    session_id = seeded["session_id"]
    _cut(library, session_id, "notes.txt")
    _cut(library, session_id, "rally-007.mp4")
    assert find_orphan_clips(library, conn, session_id) == []


def test_one_sources_rallies_do_not_orphan_anothers_clips(library, conn, seeded):
    """clips/ is per session, and a session holds several sources. The name
    carries the source index precisely so a second source's clips are not
    stranded by the first source's spans."""
    session_id = seeded["session_id"]
    second_id, second_idx = add_source(
        conn, session_id, recorded_at="2026-08-18T11:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9001.MOV",
    )
    replace_rallies(conn, session_id, second_id, [Interval(1000, 5000, 0.9)])
    _cut(library, session_id, clip_relpath(second_idx, 1000, 5000))
    _cut(library, session_id, clip_relpath(seeded["idx"], 1000, 5000))
    assert find_orphan_clips(library, conn, session_id) == []


def test_a_session_that_never_cut_a_clip_has_no_orphans(library, conn, seeded):
    # clips/ is created by the first encode, so its absence is the normal
    # state of a session, not an error.
    assert not library.clips_dir(seeded["session_id"]).exists()
    assert find_orphan_clips(library, conn, seeded["session_id"]) == []


def test_deleting_orphans_removes_exactly_those_files(library, conn, seeded):
    session_id, idx = seeded["session_id"], seeded["idx"]
    for iv in SPANS:
        _cut(library, session_id, clip_relpath(idx, iv.start_ms, iv.end_ms))
    replace_rallies(conn, session_id, seeded["source_id"], [SPANS[0], SPANS[2]])

    orphans = find_orphan_clips(library, conn, session_id)
    assert delete_orphan_clips(library, conn, orphans) == 1
    assert not (library.clips_dir(session_id) / clip_relpath(idx, 9000, 14000)).exists()
    assert (library.clips_dir(session_id) / clip_relpath(idx, 1000, 5000)).exists()
    assert (library.clips_dir(session_id) / clip_relpath(idx, 20000, 26000)).exists()


def test_deleting_an_orphan_clears_the_clip_path_that_pointed_at_it(library, conn, seeded):
    """rallies.clip_path records what WAS cut, deliberately not what the
    current bounds imply. A drag moves the bounds without moving clip_path,
    so the file it names can be swept as an orphan while the column still
    claims a clip exists -- and _carried_clip_path copies that claim onto
    the new row at every exact-span re-segment, so it outlives the sweep
    that invalidated it unless the sweep clears it.
    """
    session_id, idx = seeded["session_id"], seeded["idx"]
    path = _cut(library, session_id, clip_relpath(idx, 9000, 14000))
    relpath = str(path.relative_to(library.root))
    rally = conn.execute(
        "SELECT id FROM rallies WHERE start_ms = 9000"
    ).fetchone()
    conn.execute("UPDATE rallies SET clip_path = ?, start_ms = 9200, end_ms = 13800"
                 " WHERE id = ?", (relpath, rally["id"]))
    conn.commit()

    orphans = find_orphan_clips(library, conn, session_id)
    assert [(o.start_ms, o.end_ms) for o in orphans] == [(9000, 14000)]
    delete_orphan_clips(library, conn, orphans)
    assert conn.execute(
        "SELECT clip_path FROM rallies WHERE id = ?", (rally["id"],)
    ).fetchone()["clip_path"] is None
