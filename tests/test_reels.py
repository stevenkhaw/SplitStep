import pytest

from splitstep.db.jobs import enqueue
from splitstep.db.rallies import replace_rallies
from splitstep.db.reels import add_items, create_reel, get_reel, mark_rendered
from splitstep.db.sessions import add_source, find_or_create_session_for_date
from splitstep.detect.segment import Interval
from splitstep.media.clips import clip_relpath
from splitstep.reels import (
    clip_paths,
    delete_rendered_file,
    missing_clip_count,
    plan_reel_export,
    rendered_file,
    resolve_items,
)


@pytest.fixture
def seeded(conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.6)])
    return {"session_id": session_id, "source_id": source_id, "idx": idx}


def _cut(library, session_id, idx, start_ms, end_ms, name=None):
    clips = library.clips_dir(session_id)
    clips.mkdir(parents=True, exist_ok=True)
    path = clips / (name or clip_relpath(idx, start_ms, end_ms))
    path.write_bytes(b"fake clip")
    return path


def test_resolve_items_reports_position_source_and_span(library, conn, seeded):
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [
        (seeded["source_id"], 9000, 14000),
        (seeded["source_id"], 1000, 5000),
    ])

    items = resolve_items(library, conn, reel["id"])

    assert [(i.start_ms, i.end_ms) for i in items] == [(9000, 14000), (1000, 5000)]
    assert [i.position for i in items] == [0, 1]
    # session_id and source_idx are what the preview needs to build a proxy
    # URL, and a reel is session-agnostic -- the item cannot answer it alone.
    assert all(i.session_id == seeded["session_id"] for i in items)
    assert all(i.source_idx == seeded["idx"] for i in items)


def test_clip_ready_is_exact_path_existence(library, conn, seeded):
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [
        (seeded["source_id"], 1000, 5000),
        (seeded["source_id"], 9000, 14000),
    ])
    _cut(library, seeded["session_id"], seeded["idx"], 1000, 5000)

    items = resolve_items(library, conn, reel["id"])

    assert [i.clip_ready for i in items] == [True, False]
    assert missing_clip_count(items) == 1


def test_a_part_file_is_not_a_clip(library, conn, seeded):
    # make_clip writes a dot-prefixed .part sibling and os.replace()s it onto
    # the real name only on success. Readiness is EXACT-PATH existence, never
    # a glob over clips/, so a live encode's output cannot be counted as a
    # finished clip. This test is what keeps a future refactor from
    # introducing the glob find_orphan_clips already had to avoid.
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 5000)])
    _cut(library, seeded["session_id"], seeded["idx"], 1000, 5000,
         name=f".{clip_relpath(seeded['idx'], 1000, 5000)[:-4]}.deadbeef.part.mp4")

    items = resolve_items(library, conn, reel["id"])
    assert items[0].clip_ready is False


def test_an_item_resolves_its_rally(library, conn, seeded):
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 5000)])
    rally = resolve_items(library, conn, reel["id"])[0].rally
    assert rally is not None
    assert rally["idx"] == 1
    assert rally["confidence"] == pytest.approx(0.8)


def test_an_orphaned_item_survives_a_resegment(library, conn, seeded):
    # §5: an item whose rally has vanished renders as orphaned but is still
    # playable and still renderable, never silently dropped. The clip on disk
    # is what the reel is made of, and it still exists.
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 5000)])
    _cut(library, seeded["session_id"], seeded["idx"], 1000, 5000)
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1200, 4800, 0.7)])

    items = resolve_items(library, conn, reel["id"])

    assert len(items) == 1
    assert items[0].rally is None
    assert items[0].clip_ready is True


def test_rally_lookup_is_exact_span_not_overlap(library, conn, seeded):
    # Deliberately not the >50% overlap rule replace_rallies uses to carry
    # flags. An item IS a clip, and the clip is named for its exact bounds --
    # a rally at (1200, 4800) is a different cut from (1000, 5000), so
    # showing its metadata here would mislabel the row.
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1100, 4900)])
    assert resolve_items(library, conn, reel["id"])[0].rally is None


def test_clip_paths_are_in_reel_order(library, conn, seeded):
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [
        (seeded["source_id"], 9000, 14000),
        (seeded["source_id"], 1000, 5000),
    ])
    items = resolve_items(library, conn, reel["id"])
    assert [p.name for p in clip_paths(library, items)] == [
        clip_relpath(seeded["idx"], 9000, 14000),
        clip_relpath(seeded["idx"], 1000, 5000),
    ]


def test_plan_reel_export_queues_only_missing_spans(library, conn, seeded):
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [
        (seeded["source_id"], 1000, 5000),
        (seeded["source_id"], 9000, 14000),
    ])
    _cut(library, seeded["session_id"], seeded["idx"], 1000, 5000)

    plan = plan_reel_export(library, conn, reel["id"])

    assert plan.already_cut == 1
    assert [(p["start_ms"], p["end_ms"]) for p in plan.pending] == [(9000, 14000)]
    assert plan.total == 2


def test_plan_reel_export_reports_in_flight_separately(library, conn, seeded):
    # The four outcomes stay four. Collapsing them is what once made a second
    # press mid-encode report everything as done.
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 5000)])
    enqueue(conn, "clip", {
        "source_id": seeded["source_id"], "start_ms": 1000, "end_ms": 5000,
    })

    plan = plan_reel_export(library, conn, reel["id"])

    assert (plan.in_flight, plan.already_cut, len(plan.pending)) == (1, 0, 0)


def test_plan_reel_export_checks_job_queue_before_disk_file(library, conn, seeded):
    # A killed-and-requeued job can leave a stale complete file at this span's
    # path from an earlier run, and reading that as "already cut" while a live
    # job is about to overwrite it would be wrong. This test pins the ordering:
    # has_pending_clip() runs BEFORE item.clip_ready, so a span with both a
    # queued job and a stale disk file reports as in_flight, not already_cut.
    # Reordering these checks would pass the previous test (no disk file) but
    # fail this one (both conditions present).
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 5000)])
    _cut(library, seeded["session_id"], seeded["idx"], 1000, 5000)
    enqueue(conn, "clip", {
        "source_id": seeded["source_id"], "start_ms": 1000, "end_ms": 5000,
    })

    plan = plan_reel_export(library, conn, reel["id"])

    assert plan.in_flight == 1
    assert plan.already_cut == 0
    assert len(plan.pending) == 0


def test_plan_reel_export_carries_rally_id_when_there_is_one(library, conn, seeded):
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 5000)])
    payload = plan_reel_export(library, conn, reel["id"]).pending[0]
    assert payload["rally_id"] is not None


def test_plan_reel_export_omits_rally_id_for_an_orphan(library, conn, seeded):
    # An orphan must stay cuttable: cutting needs a source and a span, and
    # nothing else. Refusing here would leave a reel permanently unrenderable
    # with no way for the user to fix it.
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 5000)])
    replace_rallies(conn, seeded["session_id"], seeded["source_id"], [])

    payload = plan_reel_export(library, conn, reel["id"]).pending[0]

    assert "rally_id" not in payload
    assert (payload["source_id"], payload["start_ms"], payload["end_ms"]) == (
        seeded["source_id"], 1000, 5000,
    )


def _render(library, conn, reel, data=b"fake mp4 bytes"):
    """A reel whose rendered_path points at a real file under reels/, the
    way handle_reel leaves one after a successful render."""
    rel = f"reels/{reel['slug']}.mp4"
    dst = library.root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    mark_rendered(conn, reel["id"], rel, set())
    return get_reel(conn, reel["id"]), dst


def test_rendered_file_is_none_when_never_rendered(library, conn):
    reel = create_reel(conn, "r")
    assert rendered_file(library, reel) is None


def test_rendered_file_resolves_a_contained_render(library, conn):
    reel = create_reel(conn, "r")
    reel, dst = _render(library, conn, reel)
    assert rendered_file(library, reel) == dst.resolve()


def test_rendered_file_rejects_an_absolute_path(library, conn, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.mp4"
    outside.write_bytes(b"top secret")
    reel = create_reel(conn, "r")
    mark_rendered(conn, reel["id"], str(outside), set())
    reel = get_reel(conn, reel["id"])

    assert rendered_file(library, reel) is None


def test_rendered_file_rejects_an_escaping_relative_path(library, conn, tmp_path):
    # library.root IS tmp_path here (the `library` fixture builds it there),
    # so this constructs a path that escapes the library root via ".." while
    # still being nominally "relative".
    outside = tmp_path.parent / f"outside-{library.root.name}.mp4"
    outside.write_bytes(b"top secret")
    reel = create_reel(conn, "r")
    mark_rendered(conn, reel["id"], f"reels/../../{outside.name}", set())
    reel = get_reel(conn, reel["id"])

    assert rendered_file(library, reel) is None


# Finding: rendered_file returned a Path without checking the file was
# still there, so api_get_reel's rendered.stat().st_size raised
# FileNotFoundError -> 500 the moment a rendered_path's file was deleted out
# from under it -- the user freeing space in Finder, this feature's whole
# motivation.
def test_rendered_file_rejects_a_file_deleted_out_from_under_it(library, conn):
    reel = create_reel(conn, "r")
    reel, dst = _render(library, conn, reel)
    dst.unlink()

    assert rendered_file(library, reel) is None


# Finding 6: a directory passes the containment check cleanly -- a path is
# relative to itself -- and used to reach delete_rendered_file's unlink(),
# which raises IsADirectoryError/PermissionError on a directory rather than
# removing anything. That 500 fired before delete_reel ran, leaving the row
# stuck with no way to remove it through the app.
def test_rendered_file_rejects_a_directory(library, conn):
    reel = create_reel(conn, "r")
    mark_rendered(conn, reel["id"], "reels", set())
    reel = get_reel(conn, reel["id"])

    assert rendered_file(library, reel) is None
    assert library.reels_dir.is_dir()


def test_delete_rendered_file_leaves_a_directory_untouched(library, conn):
    reel = create_reel(conn, "r")
    mark_rendered(conn, reel["id"], "reels", set())
    reel = get_reel(conn, reel["id"])

    assert delete_rendered_file(library, reel) is False
    assert library.reels_dir.is_dir()


def test_delete_rendered_file_removes_a_contained_render(library, conn):
    reel = create_reel(conn, "r")
    reel, dst = _render(library, conn, reel)

    assert delete_rendered_file(library, reel) is True
    assert not dst.exists()


def test_delete_rendered_file_reports_false_when_never_rendered(library, conn):
    reel = create_reel(conn, "r")
    assert delete_rendered_file(library, reel) is False


def test_delete_rendered_file_reports_false_when_the_file_is_already_gone(library, conn):
    # A `rendered_path` can outlive its file (a hand-deleted render, or a
    # prior partial cleanup) -- delete must report this honestly rather than
    # raising on a missing file.
    reel = create_reel(conn, "r")
    reel, dst = _render(library, conn, reel)
    dst.unlink()

    assert delete_rendered_file(library, reel) is False


def test_delete_rendered_file_leaves_an_absolute_escape_untouched(
    library, conn, tmp_path_factory
):
    outside = tmp_path_factory.mktemp("outside") / "secret.mp4"
    outside.write_bytes(b"top secret")
    reel = create_reel(conn, "r")
    mark_rendered(conn, reel["id"], str(outside), set())
    reel = get_reel(conn, reel["id"])

    assert delete_rendered_file(library, reel) is False
    assert outside.exists()


def test_delete_rendered_file_leaves_a_relative_escape_untouched(library, conn, tmp_path):
    outside = tmp_path.parent / f"outside-{library.root.name}.mp4"
    outside.write_bytes(b"top secret")
    reel = create_reel(conn, "r")
    mark_rendered(conn, reel["id"], f"reels/../../{outside.name}", set())
    reel = get_reel(conn, reel["id"])

    assert delete_rendered_file(library, reel) is False
    assert outside.exists()
