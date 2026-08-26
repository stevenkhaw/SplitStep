import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from splitstep.config import Library
from splitstep.db.jobs import has_pending_clip
from splitstep.db.sessions import get_source
from splitstep.media.clips import clip_relpath, parse_clip_name

log = logging.getLogger(__name__)

SETS = ("points", "starred")


def column_for(which: str) -> str:
    """The rallies column that `which` selects.

    The result is interpolated directly into a WHERE clause via f-string in
    `plan_export`, never bound as a parameter -- this guard is what keeps
    that safe, so it must run before the ternary on *every* call, not just
    at the API edge. `ExportBody.check_which` also rejects a bad `which`
    before the route runs, but that is a second, independent gate on the
    same string; it does not make this one redundant, and a caller that
    reaches this function some other way (a script, `model_construct()`, a
    copy-pasted query elsewhere) must not be able to skip it.
    """
    if which not in SETS:
        raise ValueError(f"which must be one of {list(SETS)}, got {which!r}")
    return "point" if which == "points" else "starred"


@dataclass(frozen=True)
class ExportPlan:
    """The outcome of sorting `which`'s rallies into pending vs. the three
    distinct reasons a rally is not pending.

    `already_cut`, `in_flight`, and `unavailable` are real, different
    outcomes -- a clip on disk, a job already working on the same span, and
    a rally whose source row is gone -- and a caller that wants to tell a
    user "0 queued, 24 already cut" needs to say that only when it is true.
    `total` is derived from the other four rather than stored or queried
    separately, so it can never drift out of agreement with them: there is
    one query's worth of rows, sorted into four bins, not two queries that
    each count and might disagree.
    """

    pending: list[dict] = field(default_factory=list)
    already_cut: int = 0
    in_flight: int = 0
    unavailable: int = 0

    @property
    def total(self) -> int:
        return len(self.pending) + self.already_cut + self.in_flight + self.unavailable


def plan_export(
    library: Library, conn: sqlite3.Connection, session_id: str, which: str
) -> ExportPlan:
    """Sort `which`'s rallies (points or starred, minus rejected) into an
    `ExportPlan`: spans with no clip yet and no clip job in flight, plus a
    count for each reason the rest were excluded.

    Incremental by construction rather than by bookkeeping: a clip either
    exists at the path its current bounds imply, or it does not. Nothing
    records staleness, because there is nothing to keep in sync -- a rally
    whose bounds moved simply resolves to a different path, which is missing.
    """
    column = column_for(which)
    rows = conn.execute(
        f"SELECT * FROM rallies WHERE session_id = ? AND {column} = 1"
        " AND rejected = 0 ORDER BY idx",
        (session_id,),
    ).fetchall()

    clips_dir = library.clips_dir(session_id)
    sources: dict[str, sqlite3.Row] = {}
    pending: list[dict] = []
    already_cut = 0
    in_flight = 0
    unavailable = 0

    for rally in rows:
        source = sources.get(rally["source_id"])
        if source is None:
            source = get_source(conn, rally["source_id"])
            if source is None:
                # A rally whose source vanished cannot be cut, now or later --
                # unavailable, not already cut. Skip rather than raise: one
                # broken row must not block exporting the rest.
                unavailable += 1
                continue
            sources[rally["source_id"]] = source

        if has_pending_clip(conn, rally["source_id"], rally["start_ms"], rally["end_ms"]):
            # Checked BEFORE the on-disk existence check below, not after.
            # make_clip encodes to a temp path and os.replace()s onto the
            # final name only once ffmpeg exits 0 (see its docstring), so in
            # the ordinary case a running job's file genuinely does not exist
            # yet at this path -- but a killed-and-requeued job (reclaim_stale)
            # can leave a *stale, already-complete* file at this exact path
            # from a run before the one now in flight, and a filesystem-first
            # check would read that as "already cut" while a live job is
            # about to overwrite it. A job for this exact span is already
            # queued or running -- in flight, not cut -- and that must win.
            in_flight += 1
            continue
        name = clip_relpath(source["idx"], rally["start_ms"], rally["end_ms"])
        if (clips_dir / name).exists():
            already_cut += 1
            continue

        pending.append({
            "source_id": rally["source_id"],
            "rally_id": rally["id"],
            "start_ms": rally["start_ms"],
            "end_ms": rally["end_ms"],
        })

    return ExportPlan(
        pending=pending, already_cut=already_cut, in_flight=in_flight, unavailable=unavailable
    )


@dataclass(frozen=True)
class OrphanClip:
    """A clip file no rally's current bounds resolve to.

    Everything here is read back out of the filename rather than looked up:
    span-derived naming is what makes an orphan self-identifying, so a
    stranded file can be reported ("source 01, 9.0s-14.0s, 38 MB") without
    a row anywhere to explain it.
    """

    path: Path
    source_idx: int
    start_ms: int
    end_ms: int
    size_bytes: int


def find_orphan_clips(
    library: Library, conn: sqlite3.Connection, session_id: str
) -> list[OrphanClip]:
    """Clips in this session's `clips/` that no rally's current bounds claim.

    The inverse of `plan_export`, and the other half of what span-derived
    naming buys. Export asks "does the file this rally implies exist"; this
    asks "does a rally imply this file". A re-segment answers no for every
    span that moved, and nothing else enumerates what it left behind -- at
    roughly 2 MB per second of clip, a few threshold sweeps is real dead
    weight on the drive.

    Membership is by name and nothing else, which is why staleness needs no
    bookkeeping: a rally whose bounds moved simply stops naming the file it
    used to. Note the flags play no part -- a clip whose rally was rejected
    or un-pointed after it was cut is not stranded, because a rally still
    holds those bounds and the verdict is one keystroke from changing back.

    `iterdir()` rather than `glob("*.mp4")`, and walked one level deep, not
    just the top: `clip_relpath` names a clip `<idx>/<start>-<end>.mp4`, one
    folder per source, so the files this function must see live a level
    below `clips_dir` now. A bare top-level `iterdir()` would silently miss
    every nested clip; a recursive walk would silently pick up whatever a
    source-index-looking directory happens to contain. So each top-level
    entry is a candidate if it is a file (a stray legacy flat clip, or
    anything else dropped directly in `clips/`), and if it is a directory
    its own files are added one level down and no further -- a dotted
    directory is skipped, the same as a dotted file, since `make_clip` never
    writes one and nothing here should be looking inside it either way.
    `parse_clip_name` is the real gate on both tiers -- it admits exactly
    the names this library writes, in either shape, so an encode in flight
    and a file the user dropped in here are both left alone, and only what
    we cut can be swept.
    """
    clips_dir = library.clips_dir(session_id)
    if not clips_dir.is_dir():
        # clips/ is created by the first encode, so its absence is the
        # ordinary state of a session rather than a problem.
        return []

    # Unioned with reel_items, not rallies alone. reels.py's ReelItem
    # docstring states the invariant this has to honour: an item whose rally
    # vanished under a re-segment "renders as orphaned and stays playable,
    # cuttable and renderable -- the clip on disk is what the reel is made
    # of, and a rally is a guess the detector re-makes every sweep." A
    # threshold sweep therefore turns every reel item into exactly the shape
    # this function used to sweep up: a clip with no matching row in
    # rallies. Span-keying is what lets a reel survive a sweep at all
    # (reel_items carries no rally_id, deliberately -- see 007's migration
    # comment); leaving reel_items out of `claimed` would make that survival
    # cosmetic, since `clips prune` would delete the bytes the very next time
    # someone ran it. Joined through sources for the idx clip_relpath needs,
    # same as the rallies half of this query.
    claimed = {
        clip_relpath(row["idx"], row["start_ms"], row["end_ms"])
        for row in conn.execute(
            "SELECT s.idx AS idx, r.start_ms AS start_ms, r.end_ms AS end_ms"
            " FROM rallies r JOIN sources s ON s.id = r.source_id"
            " WHERE r.session_id = ?"
            " UNION"
            " SELECT s.idx AS idx, i.start_ms AS start_ms, i.end_ms AS end_ms"
            " FROM reel_items i JOIN sources s ON s.id = i.source_id"
            " WHERE s.session_id = ?",
            (session_id, session_id),
        )
    }

    candidates: list[Path] = []
    for entry in sorted(clips_dir.iterdir()):
        if entry.is_file():
            candidates.append(entry)
        elif entry.is_dir() and not entry.name.startswith("."):
            candidates.extend(p for p in sorted(entry.iterdir()) if p.is_file())

    orphans = []
    for path in candidates:
        rel = str(path.relative_to(clips_dir))
        if rel in claimed:
            continue
        parsed = parse_clip_name(rel)
        if parsed is None:
            continue
        source_idx, start_ms, end_ms = parsed
        orphans.append(OrphanClip(
            path=path, source_idx=source_idx, start_ms=start_ms, end_ms=end_ms,
            size_bytes=path.stat().st_size,
        ))
    return orphans


def delete_orphan_clips(
    library: Library, conn: sqlite3.Connection, orphans: list[OrphanClip]
) -> int:
    """Delete the given orphans and return how many files were removed.

    Takes the list rather than re-deriving it, so what a caller printed is
    exactly what it deletes -- re-running the sweep between the two would
    leave a window for the set to change under a user who has already been
    shown it and said yes.

    `clip_path` is cleared BEFORE the file goes, and that order is
    deliberate. The column records what WAS cut rather than what the current
    bounds imply (see `set_clip_path`), so a rally whose bounds were dragged
    still names the file being swept -- and `_carried_clip_path` copies it
    onto the new row on every exact-span re-segment, so a stale one does not
    decay with time, it propagates. Clearing first means the worst a failed
    unlink can leave behind is a column that understates what is on disk,
    which is the harmless direction; the other order leaves a row asserting
    a clip that is not there.
    """
    deleted = 0
    for orphan in orphans:
        conn.execute(
            "UPDATE rallies SET clip_path = NULL WHERE clip_path = ?",
            (str(orphan.path.relative_to(library.root)),),
        )
        conn.commit()
        try:
            orphan.path.unlink()
        except FileNotFoundError:
            # Someone else got there first. Not an error, and not a reason to
            # skip the clip_path clear above -- a column naming a file that
            # is already gone is precisely the dangling reference this exists
            # to prevent.
            continue
        deleted += 1
    return deleted


def reconcile_clip_layout(library: Library, conn: sqlite3.Connection) -> int:
    """One-time move of legacy flat clips into per-source folders.

    File layout, not schema, which is why this is not a numbered migration --
    migrations cannot move files. Runs at serve startup and before every
    clips CLI command, and must run before anything calls plan_export in the
    same process: a claimed flat clip that has not moved yet would read as
    "not cut" and trigger a pointless re-encode. Idempotent -- a swept
    library has no top-level files matching the legacy shape, so the walk
    finds nothing. A collision (nested target already exists) leaves the
    flat file in place and logs it rather than deleting data; the orphan
    tooling can see it (parse_clip_name still admits the legacy shape).
    Renames are same-directory-tree, hence atomic on one filesystem, and the
    matching rallies.clip_path row is rewritten in the same pass so the
    column keeps naming a file that exists.
    """
    moved = 0
    for clips_dir in sorted(library.sessions_dir.glob("*/clips")):
        for path in sorted(clips_dir.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            parsed = parse_clip_name(path.name)
            if parsed is None:
                continue
            source_idx, start_ms, end_ms = parsed
            target = clips_dir / clip_relpath(source_idx, start_ms, end_ms)
            if target.exists():
                log.warning("not moving %s: %s already exists", path, target)
                continue
            target.parent.mkdir(exist_ok=True)
            old_rel = str(path.relative_to(library.root))
            path.rename(target)
            conn.execute(
                "UPDATE rallies SET clip_path = ? WHERE clip_path = ?",
                (str(target.relative_to(library.root)), old_rel),
            )
            conn.commit()
            moved += 1
    if moved:
        log.info("moved %d clip(s) into per-source folders", moved)
    return moved
