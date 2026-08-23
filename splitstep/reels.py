import sqlite3
from dataclasses import dataclass
from pathlib import Path

from splitstep.config import Library
from splitstep.db.jobs import has_pending_clip
from splitstep.db.reels import list_items
from splitstep.export import ExportPlan
from splitstep.media.clips import clip_relpath


@dataclass(frozen=True)
class ReelItem:
    """One reel row, resolved against the library and the rally table.

    `session_id` and `source_idx` are joined in rather than stored: a reel is
    session-agnostic by design (§5), so the item alone cannot say where its
    footage lives, and both the clip path and the preview's proxy URL need
    it. The join is safe without a LEFT: reel_items cascades on source_id, so
    an item whose source is gone is gone too.

    `rally` is None for an ORPHAN -- an item whose span no rally holds any
    more, which a threshold sweep produces routinely. It renders as orphaned
    and stays playable, cuttable and renderable: the clip on disk is what the
    reel is made of, and a rally is a guess the detector re-makes every sweep.
    """

    source_id: str
    session_id: str
    source_idx: int
    start_ms: int
    end_ms: int
    position: int
    clip_relpath: str
    clip_ready: bool
    rally: dict | None

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def resolve_items(
    library: Library, conn: sqlite3.Connection, reel_id: str
) -> list[ReelItem]:
    """Every item of a reel, in order, with its clip status and rally.

    Readiness is the existence of ONE exact path, never a listing of
    `clips/`. That is not an optimization: `make_clip` writes a dot-prefixed
    `.part` sibling while an encode is in flight, so a glob would count a
    live (or dead) temp file as a finished clip. Asking for the exact name
    the span implies cannot make that mistake -- and it is the same question
    plan_export asks, so the builder and the exporter can never disagree
    about what "cut" means.
    """
    rows = list_items(conn, reel_id)
    if not rows:
        return []

    sources = {
        r["id"]: r
        for r in conn.execute(
            "SELECT id, session_id, idx FROM sources WHERE id IN"
            f" ({','.join('?' * len({r['source_id'] for r in rows}))})",
            tuple({r["source_id"] for r in rows}),
        )
    }

    items: list[ReelItem] = []
    for row in rows:
        source = sources[row["source_id"]]
        name = clip_relpath(source["idx"], row["start_ms"], row["end_ms"])
        # Exact span, deliberately not the >50% overlap rule replace_rallies
        # uses to carry flags across a sweep. An item IS a clip, and the clip
        # is named for these exact bounds -- showing a neighbouring rally's
        # duration and confidence here would mislabel the row.
        rally = conn.execute(
            "SELECT * FROM rallies WHERE source_id = ? AND start_ms = ? AND end_ms = ?",
            (row["source_id"], row["start_ms"], row["end_ms"]),
        ).fetchone()
        items.append(ReelItem(
            source_id=row["source_id"],
            session_id=source["session_id"],
            source_idx=source["idx"],
            start_ms=row["start_ms"],
            end_ms=row["end_ms"],
            position=row["position"],
            clip_relpath=name,
            clip_ready=(library.clips_dir(source["session_id"]) / name).exists(),
            rally=dict(rally) if rally is not None else None,
        ))
    return items


def missing_clip_count(items: list[ReelItem]) -> int:
    return sum(1 for i in items if not i.clip_ready)


def rendered_file(library: Library, reel: sqlite3.Row) -> Path | None:
    """The reel's rendered file, or None if there isn't one -- or if
    `rendered_path` does not resolve inside `library.reels_dir`.

    Stricter than the read-time check api_reel_media used to inline against
    `library.root`: a reel's render always lives in `reels/`, and this value
    is about to be handed to `unlink()` by delete_rendered_file, so "somewhere
    in the library" is not tight enough for that caller. `Path.__truediv__`
    silently discards the left operand when the right is absolute --
    `library.root / "/etc/passwd"` is just `Path("/etc/passwd")` -- so
    `rendered_path` must be resolved before the containment check runs, not
    just joined and trusted: an absolute value, or a relative one escaping
    via "..", has to fail `is_relative_to` rather than slip through.

    Today only mark_rendered writes this column, and it always stores a
    library-relative `reels/<slug>.mp4`. But that is a guarantee held by one
    write site's good behaviour, not by anything at a site that opens (or
    deletes) the file -- a second writer, a bug in mark_rendered, or a
    hand-edited row would defeat it silently otherwise. Centralizing the
    check here, rather than re-inlining it at api_reel_media and again at
    delete_rendered_file, is what keeps a read path and a delete path from
    drifting on a security-relevant check.

    Also requires `is_file()`. Two rows this rejects that containment alone
    would pass through: a `rendered_path` whose file was deleted out from
    under it (the user freeing space in Finder -- this feature's whole
    reason to exist), and one naming a directory under `reels/` (`reels/`
    itself, say) -- a path is relative to itself, so containment alone
    cannot catch that. The first used to reach `api_get_reel`'s
    `rendered.stat().st_size`, which raises `FileNotFoundError` -- a 500
    on every subsequent GET, with no Delete button in the UI's error state
    to remove the row by (see Reel.svelte). The second used to reach
    `delete_rendered_file`'s `unlink()`, which raises
    `IsADirectoryError`/`PermissionError` instead -- a 500 that fires before
    `delete_reel`, leaving THAT row stuck too. Checking here instead means
    every caller sees a plain None for "nothing real to serve or delete",
    exactly as if the reel had never been rendered: `api_get_reel` folds it
    into `rendered_bytes: None`, and `api_reel_media`'s existing
    `path is None` branch 404s it without needing a special case of its own.
    """
    if reel["rendered_path"] is None:
        return None
    path = (library.root / reel["rendered_path"]).resolve()
    if not path.is_relative_to(library.reels_dir.resolve()):
        return None
    if not path.is_file():
        return None
    return path


def delete_rendered_file(library: Library, reel: sqlite3.Row) -> bool:
    """Unlink the reel's rendered file, if it has one that passes
    `rendered_file`'s containment check. Returns whether a file was removed.

    A `rendered_path` that fails containment is left exactly alone, never
    unlinked: a bad row must not become grounds to delete a file outside
    `reels/`. See `rendered_file` for how that could happen at all.
    """
    path = rendered_file(library, reel)
    if path is None or not path.exists():
        return False
    path.unlink()
    return True


def clip_paths(library: Library, items: list[ReelItem]) -> list[Path]:
    """Absolute clip paths in reel order -- the concat demuxer's input list."""
    return [library.clips_dir(i.session_id) / i.clip_relpath for i in items]


def plan_reel_export(
    library: Library, conn: sqlite3.Connection, reel_id: str
) -> ExportPlan:
    """Sort a reel's items into spans needing a cut, plus why the rest do not.

    Reuses Plan A's ExportPlan rather than reimplementing the decision, and
    keeps its four outcomes four: collapsing `queued` / `already_cut` /
    `in_flight` into one bucket is what once made a second press mid-encode
    report everything as done.

    `unavailable` is structurally always 0 here and the field is kept anyway,
    so the reel and session endpoints return one shape. A session export can
    hit a rally whose source row is gone; a reel item cannot, because
    reel_items cascades on source_id.
    """
    pending: list[dict] = []
    already_cut = 0
    in_flight = 0

    for item in resolve_items(library, conn, reel_id):
        if has_pending_clip(conn, item.source_id, item.start_ms, item.end_ms):
            # Checked BEFORE the on-disk check, exactly as plan_export does:
            # a killed-and-requeued job can leave a stale complete file at
            # this path from an earlier run, and reading that as "already
            # cut" while a live job is about to overwrite it is wrong.
            in_flight += 1
            continue
        if item.clip_ready:
            already_cut += 1
            continue
        payload = {
            "source_id": item.source_id,
            "start_ms": item.start_ms,
            "end_ms": item.end_ms,
        }
        # Only when there is a rally to record it on -- handle_clip treats
        # the key as optional precisely so an orphan stays cuttable.
        if item.rally is not None:
            payload["rally_id"] = item.rally["id"]
        pending.append(payload)

    return ExportPlan(pending=pending, already_cut=already_cut, in_flight=in_flight)
