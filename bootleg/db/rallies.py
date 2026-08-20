import sqlite3
import uuid
from datetime import UTC, datetime

from bootleg.detect.segment import Interval

STAR_OVERLAP_MIN = 0.5


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _overlap_fraction(a_start: int, a_end: int, b_start: int, b_end: int) -> float:
    overlap = min(a_end, b_end) - max(a_start, b_start)
    if overlap <= 0:
        return 0.0
    return overlap / max(1, min(a_end - a_start, b_end - b_start))


def _overlaps_any(iv: Interval, rows: list[sqlite3.Row], flag: str) -> bool:
    return any(
        r[flag]
        and _overlap_fraction(iv.start_ms, iv.end_ms, r["start_ms"], r["end_ms"])
        >= STAR_OVERLAP_MIN
        for r in rows
    )


def replace_rallies(
    conn: sqlite3.Connection,
    session_id: str,
    source_id: str,
    intervals: list[Interval],
) -> int:
    """Rewrite one source's rallies, carrying stars and rejections across by
    overlap.

    Manual boundary edits are intentionally not preserved — the caller
    confirms that loss before calling.

    The star/rejected read-back, the delete, the inserts, and the renumber
    all run as one transaction: on any exception the connection is rolled
    back to its state before this call and the exception is re-raised, so a
    half-applied rewrite can never sit uncommitted on the shared connection
    waiting for some unrelated later commit to persist it.
    """
    try:
        old = conn.execute(
            "SELECT start_ms, end_ms, starred, rejected FROM rallies"
            " WHERE source_id = ? AND (starred = 1 OR rejected = 1)",
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
            # idx is a temporary, per-row-unique negative placeholder so a batch of
            # several new rows never collides with itself under UNIQUE(session_id,
            # idx) before _renumber() assigns the real sequential values below.
            conn.execute(
                "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
                "det_start_ms,det_end_ms,confidence,starred,rejected)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, session_id, source_id, -placeholder_idx, iv.start_ms,
                 iv.end_ms, iv.start_ms, iv.end_ms, iv.confidence,
                 int(starred), int(rejected)),
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
