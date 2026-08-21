import sqlite3
from dataclasses import dataclass, field

from bootleg.config import Library
from bootleg.db.jobs import has_pending_clip
from bootleg.db.sessions import get_source
from bootleg.media.clips import clip_relpath

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
