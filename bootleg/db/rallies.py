import sqlite3
import uuid
from datetime import UTC, datetime

from bootleg.detect.segment import Interval

STAR_OVERLAP_MIN = 0.5


def _now() -> str:
    return datetime.now(UTC).isoformat()


def overlap_fraction(a_start: int, a_end: int, b_start: int, b_end: int) -> float:
    """Overlap as a fraction of the *shorter* of the two spans.

    Public because the label scorer matches candidate intervals to labelled
    spans by the same rule replace_rallies uses to carry stars across a
    re-segment. One definition, so a rally that would inherit a star and a
    candidate that would count against a label can never disagree about what
    "the same rally" means.
    """
    overlap = min(a_end, b_end) - max(a_start, b_start)
    if overlap <= 0:
        return 0.0
    return overlap / max(1, min(a_end - a_start, b_end - b_start))


def _overlaps_any(iv: Interval, rows: list[sqlite3.Row], flag: str) -> bool:
    return any(
        r[flag]
        and overlap_fraction(iv.start_ms, iv.end_ms, r["start_ms"], r["end_ms"])
        >= STAR_OVERLAP_MIN
        for r in rows
    )


def _carried_clip_path(iv: Interval, rows: list[sqlite3.Row]) -> str | None:
    """clip_path for `iv` if an old row's bounds are its EXACT bounds, else None.

    Deliberately not the >50% overlap rule starred/rejected/point use. Those
    three are judgements about a rally that survive it shifting slightly
    under a re-segment; clip_path is a fact about one specific span --
    set_clip_path's docstring: "what WAS cut" -- and a clip cut for
    (1000, 5000) is not what was cut for (1200, 4800), even though a reviewer
    would call them the same rally. Carrying it across a fuzzy-matched shift
    would silently point a rally at a clip whose span it no longer has.
    """
    for r in rows:
        if r["clip_path"] is not None and r["start_ms"] == iv.start_ms and r["end_ms"] == iv.end_ms:
            return r["clip_path"]
    return None


def replace_rallies(
    conn: sqlite3.Connection,
    session_id: str,
    source_id: str,
    intervals: list[Interval],
) -> int:
    """Rewrite one source's rallies, carrying stars, rejections and points
    across by overlap, and clip_path across by exact span match (see
    _carried_clip_path).

    Manual boundary edits are intentionally not preserved — the caller
    confirms that loss before calling.

    The star/rejected read-back, the delete, the inserts, and the renumber
    all run as one transaction: on any exception the connection is rolled
    back to its state before this call and the exception is re-raised, so a
    half-applied rewrite can never sit uncommitted on the shared connection
    waiting for some unrelated later commit to persist it.
    """
    try:
        # clip_path IS NOT NULL is an extra carry-over candidate alongside the
        # three flags, not an inconsistency with them: a clip can be cut for a
        # rally that a reviewer later un-stars/un-points (the flag changes;
        # nothing re-cuts or deletes the file), so restricting this read-back
        # to starred/rejected/point rows would silently drop clip_path for a
        # real file still sitting on disk at that exact span.
        old = conn.execute(
            "SELECT start_ms, end_ms, starred, rejected, point, clip_path FROM rallies"
            " WHERE source_id = ? AND (starred = 1 OR rejected = 1 OR point = 1"
            " OR clip_path IS NOT NULL)",
            (source_id,),
        ).fetchall()

        conn.execute("DELETE FROM rallies WHERE source_id = ?", (source_id,))

        for placeholder_idx, iv in enumerate(intervals, start=1):
            starred = _overlaps_any(iv, old, "starred")
            # A rally overlapping both an old starred segment and an old
            # rejected segment keeps the star and drops the rejection --
            # starred takes priority when a new segment plausibly matches
            # both carry-over candidates.
            rejected = False if starred else _overlaps_any(iv, old, "rejected")
            # Carried independently of starred/rejected: `point` answers "was a
            # point played out here", which is orthogonal to whether the clip
            # is a highlight or a bad detection. Without this line the first
            # threshold sweep silently discards the reviewer's whole
            # tiebreaker -- the same class of loss the star carry-over exists
            # to prevent.
            point = _overlaps_any(iv, old, "point")
            clip_path = _carried_clip_path(iv, old)
            # idx is a temporary, per-row-unique negative placeholder so a batch of
            # several new rows never collides with itself under UNIQUE(session_id,
            # idx) before _renumber() assigns the real sequential values below.
            conn.execute(
                "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
                "det_start_ms,det_end_ms,confidence,starred,rejected,point,clip_path)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, session_id, source_id, -placeholder_idx, iv.start_ms,
                 iv.end_ms, iv.start_ms, iv.end_ms, iv.confidence,
                 int(starred), int(rejected), int(point), clip_path),
            )

        _renumber(conn, session_id)
    except Exception:
        # Any failure must roll back before propagating -- see docstring.
        conn.rollback()
        raise
    conn.commit()
    return len(intervals)


def _renumber(conn: sqlite3.Connection, session_id: str) -> None:
    rows = conn.execute(
        "SELECT r.id FROM rallies r JOIN sources s ON s.id = r.source_id"
        " WHERE r.session_id = ? ORDER BY s.idx, r.start_ms",
        (session_id,),
    ).fetchall()

    # Two-phase renumber. replace_rallies only rewrites one source at a
    # time, so sibling sources' rows still hold their old positive idx
    # here. Assigning final 1..N values in a single pass can walk straight
    # into a sibling's still-live idx and violate UNIQUE(session_id, idx)
    # (e.g. a non-last source growing its rally count). Push every rally in
    # the session -- not just the ones replace_rallies just inserted -- into
    # a disjoint negative range first, the same placeholder technique used
    # for the INSERT batch above, then assign the real values once no row
    # holds a value another row still needs.
    total = len(rows)
    for i, row in enumerate(rows, start=1):
        conn.execute("UPDATE rallies SET idx = ? WHERE id = ?", (-(total + i), row["id"]))
    for i, row in enumerate(rows, start=1):
        conn.execute("UPDATE rallies SET idx = ? WHERE id = ?", (i, row["id"]))


def list_rallies(conn: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM rallies WHERE session_id = ? ORDER BY idx", (session_id,)
    ).fetchall()


def set_star(conn: sqlite3.Connection, rally_id: str, starred: bool) -> None:
    conn.execute(
        "UPDATE rallies SET starred = ?, reviewed_at = COALESCE(reviewed_at, ?)"
        " WHERE id = ?",
        (int(starred), _now(), rally_id),
    )
    conn.commit()


def set_point(conn: sqlite3.Connection, rally_id: str, point: bool) -> None:
    """Mark (or unmark) a rally as a point that was played out.

    Stamps reviewed_at through the same COALESCE set_star/set_rejected use.
    All three flags are rulings on the clip, and reviewed_at records that a
    human has ruled on a rally at all -- so a reviewer who marks every point
    of a tiebreaker and stars none must still end with a reviewed session.
    """
    conn.execute(
        "UPDATE rallies SET point = ?, reviewed_at = COALESCE(reviewed_at, ?)"
        " WHERE id = ?",
        (int(point), _now(), rally_id),
    )
    conn.commit()


def set_rejected(conn: sqlite3.Connection, rally_id: str, rejected: bool) -> None:
    conn.execute(
        "UPDATE rallies SET rejected = ?, reviewed_at = COALESCE(reviewed_at, ?)"
        " WHERE id = ?",
        (int(rejected), _now(), rally_id),
    )
    conn.commit()


def mark_reviewed(conn: sqlite3.Connection, rally_id: str) -> None:
    conn.execute(
        "UPDATE rallies SET reviewed_at = COALESCE(reviewed_at, ?) WHERE id = ?",
        (_now(), rally_id),
    )
    conn.commit()


def set_bounds(conn: sqlite3.Connection, rally_id: str,
               start_ms: int, end_ms: int) -> None:
    """Update working bounds only. det_* columns are immutable training data."""
    conn.execute(
        "UPDATE rallies SET start_ms = ?, end_ms = ? WHERE id = ?",
        (start_ms, end_ms, rally_id),
    )
    conn.commit()


def set_clip_path(conn: sqlite3.Connection, rally_id: str, clip_path: str) -> None:
    """Record the library-relative path of the clip cut for this rally.

    Stored rather than derived because a rally's bounds can move after its
    clip was cut -- the path here is what WAS cut, while clip_relpath() of the
    current bounds is what SHOULD be.

    Nothing reads it today. It was written for Reclaim Space, which is now
    rejected rather than deferred, and every other consumer asks the
    filesystem instead: plan_export tests for the file at the current
    bounds, and so does the reel builder's ready/missing badge. It is kept
    because it is the only record of what was cut for a span the rally no
    longer has -- which is exactly what `clips prune` needs to null out when
    it deletes that file.
    """
    conn.execute("UPDATE rallies SET clip_path = ? WHERE id = ?", (clip_path, rally_id))
    conn.commit()
