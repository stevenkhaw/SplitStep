import math
import sqlite3
import uuid
from datetime import UTC, datetime

from bootleg.detect.segment import Interval

STAR_OVERLAP_MIN = 0.5

# The longest note that still renders as two lines inside the caption pill at
# 4K without shrinking the type. Enforced here and again at the API boundary
# (NoteBody), and mirrored in web/src/lib/notes.ts -- a note that cannot be
# rendered must never reach the database, whoever is writing it.
NOTE_MAX_CHARS = 120


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


def _carried_note(iv: Interval, rows: list[sqlite3.Row]) -> str:
    """The note for `iv` from the best-overlapping old row, else ''.

    The >50% rule starred/rejected/point use, not clip_path's exact-span
    rule: a note is a judgement about a rally, and a rally that shifts by a
    few hundred milliseconds under a new threshold is the same rally the
    reviewer wrote about. clip_path is different because it names a file cut
    for one specific span.

    Best overlap rather than first match, unlike _overlaps_any. Those three
    are booleans, so any qualifying row gives the same answer; a note is a
    string, and two old rallies can both clear 50% of one merged new span. The
    answer must not depend on the order sqlite returned the rows in.

    Ranking has two stages, and only the first is a qualifier: overlap_fraction
    (the same >50% rule the flags use, normalized so a short old rally and a
    long one need the same relative coverage to count) decides which rows are
    even in the running. Among those, raw millisecond overlap is not a
    tie-break -- it is the sole ranking signal, full stop. Two qualifying rows
    with different fractions are still ranked by milliseconds alone, because
    overlap_fraction divides by the *shorter* span, so any old rally fully
    contained in the new one scores a flat 1.0 regardless of its own length --
    two contained old rallies of different sizes are indistinguishable by
    fraction alone, and raw overlap is the only signal left at that point. It
    is also the more natural "longest overlap" a reviewer would name if asked
    which old rally a merged span best represents.

    A third, genuine tie-break -- smaller start_ms wins -- makes the ranking a
    total order. Two old rallies of equal duration absorbed into one merged
    new span produce the identical raw overlap: old (0, 600) and old
    (400, 1000) both overlap a new (0, 1000) span by exactly 600ms. The
    read-back SELECT carries no ORDER BY, so without this the winner would be
    whichever row sqlite happened to list first -- not an answer.
    """
    # best_start_ms starts at +inf, not None: `r["start_ms"] < best_start_ms`
    # below is a real numeric comparison on every qualifying row, including
    # the first. A None start was only ever safe because the first row to
    # clear the >= STAR_OVERLAP_MIN gate always has overlap_ms > 0 ==
    # best_overlap_ms, so the `overlap_ms > best_overlap_ms` disjunct wins
    # before the None comparison is reached -- a short-circuit that silently
    # depended on STAR_OVERLAP_MIN being greater than zero.
    best_note, best_overlap_ms, best_start_ms = "", 0, math.inf
    for r in rows:
        if not r["note"]:
            continue
        f = overlap_fraction(iv.start_ms, iv.end_ms, r["start_ms"], r["end_ms"])
        if f < STAR_OVERLAP_MIN:
            continue
        # Recomputed rather than reused from inside overlap_fraction: that
        # function's signature is shared with the label scorer and must not
        # change shape just to also hand back its numerator. The duplication
        # here is deliberate, not an oversight.
        overlap_ms = min(iv.end_ms, r["end_ms"]) - max(iv.start_ms, r["start_ms"])
        better = overlap_ms > best_overlap_ms or (
            overlap_ms == best_overlap_ms and r["start_ms"] < best_start_ms
        )
        if better:
            best_note, best_overlap_ms, best_start_ms = r["note"], overlap_ms, r["start_ms"]
    return best_note


def replace_rallies(
    conn: sqlite3.Connection,
    session_id: str,
    source_id: str,
    intervals: list[Interval],
) -> int:
    """Rewrite one source's rallies, carrying stars, rejections, points and
    notes across by overlap, and clip_path across by exact span match (see
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
            "SELECT start_ms, end_ms, starred, rejected, point, clip_path, note FROM rallies"
            " WHERE source_id = ? AND (starred = 1 OR rejected = 1 OR point = 1"
            " OR clip_path IS NOT NULL OR note != '')",
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
            note = _carried_note(iv, old)
            # idx is a temporary, per-row-unique negative placeholder so a batch of
            # several new rows never collides with itself under UNIQUE(session_id,
            # idx) before _renumber() assigns the real sequential values below.
            conn.execute(
                "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
                "det_start_ms,det_end_ms,confidence,starred,rejected,point,clip_path,note)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, session_id, source_id, -placeholder_idx, iv.start_ms,
                 iv.end_ms, iv.start_ms, iv.end_ms, iv.confidence,
                 int(starred), int(rejected), int(point), clip_path, note),
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
    # seen_at is stamped alongside reviewed_at, not just reviewed_at alone:
    # a starred rally was necessarily looked at, and without this a rally
    # starred on the very first pass (never skipped past) would still read
    # as unseen -- see set_seen's docstring for the column split this
    # protects. One `now`, not two _now() calls: a rally starred for the
    # first time sets both columns in the same statement, and they should
    # carry the identical instant, not two calls' worth of clock drift.
    now = _now()
    conn.execute(
        "UPDATE rallies SET starred = ?, reviewed_at = COALESCE(reviewed_at, ?),"
        " seen_at = COALESCE(seen_at, ?) WHERE id = ?",
        (int(starred), now, now, rally_id),
    )
    conn.commit()


def set_point(conn: sqlite3.Connection, rally_id: str, point: bool) -> None:
    """Mark (or unmark) a rally as a point that was played out.

    Stamps reviewed_at through the same COALESCE set_star/set_rejected use.
    All three flags are rulings on the clip, and reviewed_at records that a
    human has ruled on a rally at all -- so a reviewer who marks every point
    of a tiebreaker and stars none must still end with a reviewed session.

    Also stamps seen_at through its own COALESCE, same reasoning as
    set_star: a ruling cannot be made on a rally nobody looked at.
    """
    now = _now()
    conn.execute(
        "UPDATE rallies SET point = ?, reviewed_at = COALESCE(reviewed_at, ?),"
        " seen_at = COALESCE(seen_at, ?) WHERE id = ?",
        (int(point), now, now, rally_id),
    )
    conn.commit()


def set_rejected(conn: sqlite3.Connection, rally_id: str, rejected: bool) -> None:
    # See set_star's comment on the added seen_at stamp, including why this
    # is one `now` shared by both COALESCEs rather than two _now() calls.
    now = _now()
    conn.execute(
        "UPDATE rallies SET rejected = ?, reviewed_at = COALESCE(reviewed_at, ?),"
        " seen_at = COALESCE(seen_at, ?) WHERE id = ?",
        (int(rejected), now, now, rally_id),
    )
    conn.commit()


def set_seen(conn: sqlite3.Connection, rally_id: str) -> None:
    """Record that a human has looked at this rally, independent of any
    ruling (star/point/reject).

    This is the other half of the reviewed_at split: reviewed_at means "a
    human ruled on this rally" and is what session status is computed from
    (refresh_session_review_status), while seen_at means "a human has looked
    at this rally at all" and is what the review queue resumes from
    (QueueController's firstUnseen). persist.ts's skip case calls this --
    stamping reviewed_at there instead, as the code used to not do at all,
    would flip a whole session to 'reviewed' off the back of a plain
    right-arrow with no judgement behind it.

    COALESCE, same shape as mark_reviewed: the first write wins, so
    revisiting an already-seen rally later in the pass does not move its
    "first seen" timestamp.
    """
    conn.execute(
        "UPDATE rallies SET seen_at = COALESCE(seen_at, ?) WHERE id = ?",
        (_now(), rally_id),
    )
    conn.commit()


def set_note(conn: sqlite3.Connection, rally_id: str, note: str) -> None:
    """Write (or clear) a rally's note.

    Deliberately does NOT stamp reviewed_at, unlike set_star/set_point/
    set_rejected. Those three are rulings on the clip and reviewed_at records
    that a human ruled on it; a note carries no verdict at all -- "check this
    later" is an ordinary thing to write -- so flipping a session to reviewed
    on the strength of one would report a judgement nobody made.
    """
    conn.execute("UPDATE rallies SET note = ? WHERE id = ?", (note, rally_id))
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
