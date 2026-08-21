import sqlite3

from bootleg.config import Library
from bootleg.db.jobs import has_pending_clip
from bootleg.db.sessions import get_source
from bootleg.media.clips import clip_relpath

SETS = ("points", "starred")


def spans_to_cut(
    library: Library, conn: sqlite3.Connection, session_id: str, which: str
) -> list[dict]:
    """The spans in `which` that have no clip yet and no clip job in flight.

    Incremental by construction rather than by bookkeeping: a clip either
    exists at the path its current bounds imply, or it does not. Nothing
    records staleness, because there is nothing to keep in sync -- a rally
    whose bounds moved simply resolves to a different path, which is missing.
    """
    if which not in SETS:
        raise ValueError(f"which must be one of {list(SETS)}, got {which!r}")

    # The f-string interpolates a column name chosen from a fixed tuple, never
    # from request data -- the guard above is what keeps that true, so it must
    # stay directly above this query rather than drifting into the caller.
    column = "point" if which == "points" else "starred"
    rows = conn.execute(
        f"SELECT * FROM rallies WHERE session_id = ? AND {column} = 1"
        " AND rejected = 0 ORDER BY idx",
        (session_id,),
    ).fetchall()

    clips_dir = library.clips_dir(session_id)
    sources: dict[str, sqlite3.Row] = {}
    pending: list[dict] = []

    for rally in rows:
        source = sources.get(rally["source_id"])
        if source is None:
            source = get_source(conn, rally["source_id"])
            if source is None:
                # A rally whose source vanished cannot be cut. Skip rather than
                # raise: one broken row must not block exporting the rest.
                continue
            sources[rally["source_id"]] = source

        name = clip_relpath(source["idx"], rally["start_ms"], rally["end_ms"])
        if (clips_dir / name).exists():
            continue
        if has_pending_clip(conn, rally["source_id"], rally["start_ms"], rally["end_ms"]):
            continue

        pending.append({
            "source_id": rally["source_id"],
            "rally_id": rally["id"],
            "start_ms": rally["start_ms"],
            "end_ms": rally["end_ms"],
        })

    return pending
