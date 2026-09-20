import math
import sqlite3
import uuid
from datetime import UTC, datetime

from splitstep.detect.segment import Interval

STAR_OVERLAP_MIN = 0.5

# The longest note that still renders as two lines inside the caption pill at
# 4K without shrinking the type. Enforced here and again at the API boundary
# (NoteBody), and mirrored in web/src/lib/notes.ts -- a note that cannot be
# rendered must never reach the database, whoever is writing it.
NOTE_MAX_CHARS = 120

# The shortest span a reviewer may hand-draw, mirroring MIN_RALLY_MS in
# web/src/lib/timeline.ts (100), where the boundary-drag clamp already lives.
# Duplicated rather than imported for the reason NOTE_MAX_CHARS is: the two
# languages cannot share a constant, and the server must not trust a client
# to have applied its own floor.
#
# Enforced here and NOT in split_rally, which is not an inconsistency: a
# split cuts a span the detector already proposed, so both halves describe
# footage something scored, and the bounds route deliberately validates only
# end_ms > start_ms (media/concat.py spells out that a hand-trimmed clip well
# under 1.5s is a real input). A hand-drawn span has no such provenance --
# nothing but the floor stands between a stray double-tap of `Enter` and a
# zero-length rally that renders as a clip nobody can play.
MIN_RALLY_MS = 100


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


def _carried_winner(iv: Interval, rows: list[sqlite3.Row]) -> str:
    """The winner for `iv` from the best-overlapping old *point*, else ''.

    Same ranking as _carried_note -- overlap_fraction qualifies, raw
    millisecond overlap ranks, smaller start_ms breaks a tie so two old
    rallies of equal duration absorbed into one merged span still resolve to
    a single answer -- restricted to rows that were points because a winner
    on a non-point row cannot exist (set_point clears it) and should not be
    invented here.
    """
    best_winner, best_overlap_ms, best_start_ms = "", 0, math.inf
    for r in rows:
        if not r["point"] or not r["winner"]:
            continue
        f = overlap_fraction(iv.start_ms, iv.end_ms, r["start_ms"], r["end_ms"])
        if f < STAR_OVERLAP_MIN:
            continue
        overlap_ms = min(iv.end_ms, r["end_ms"]) - max(iv.start_ms, r["start_ms"])
        better = overlap_ms > best_overlap_ms or (
            overlap_ms == best_overlap_ms and r["start_ms"] < best_start_ms
        )
        if better:
            best_winner, best_overlap_ms, best_start_ms = r["winner"], overlap_ms, r["start_ms"]
    return best_winner


def replace_rallies(
    conn: sqlite3.Connection,
    session_id: str,
    source_id: str,
    intervals: list[Interval],
) -> int:
    """Rewrite one source's rallies, carrying stars, rejections, points,
    winners and notes across by overlap, and clip_path across by exact span
    match (see _carried_clip_path).

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
            "SELECT start_ms, end_ms, starred, rejected, point, winner, clip_path, note"
            " FROM rallies"
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
            # The winner rides with the point, by the same overlap rule, and
            # only with it: a new rally that loses `point` loses `winner`
            # too, so the two columns can never disagree about whether a
            # point was scored. Best-overlap like the note, not first-match
            # like the flags -- two old points can both clear 50% of one
            # merged span and the answer must not depend on row order.
            winner = _carried_winner(iv, old) if point else ""
            clip_path = _carried_clip_path(iv, old)
            note = _carried_note(iv, old)
            # idx is a temporary, per-row-unique negative placeholder so a batch of
            # several new rows never collides with itself under UNIQUE(session_id,
            # idx) before _renumber() assigns the real sequential values below.
            conn.execute(
                "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
                "det_start_ms,det_end_ms,confidence,starred,rejected,point,winner,"
                "clip_path,note)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, session_id, source_id, -placeholder_idx, iv.start_ms,
                 iv.end_ms, iv.start_ms, iv.end_ms, iv.confidence,
                 int(starred), int(rejected), int(point), winner, clip_path, note),
            )

        _renumber(conn, session_id)
    except Exception:
        # Any failure must roll back before propagating -- see docstring.
        conn.rollback()
        raise
    conn.commit()
    return len(intervals)


def split_rally(conn: sqlite3.Connection, rally_id: str, at_ms: int) -> str:
    """Cut one rally in two at `at_ms`. Returns the new (second) rally's id.

    The second half carries NO detector span. rally_labels anchors on
    (source_id, det_start_ms, det_end_ms) -- the detector's own span, which
    is what lets the corpus survive replace_rallies -- so two halves
    inheriting one det span would be the same row in the corpus, and
    labelling the second would silently overwrite the judgement on the
    first. Giving each half its own det span covering its own bounds is
    worse: det_* is immutable and records what the detector ORIGINALLY
    guessed, so spans it never produced are fabricated training data.

    `at_ms` must sit strictly inside the rally, so both halves are
    non-empty. The MIN_RALLY_MS floor is deliberately not enforced here --
    the bounds route validates only end_ms > start_ms and leaves the floor
    to clampMinGap client-side (see media/concat.py, which spells out that a
    hand-trimmed clip well under 1.5s is a real input). This layer rejects
    what is incoherent, not what is merely short.

    Every review flag is inherited, which is not a fresh judgement call: it
    is what replace_rallies' own carry-over produces for these two
    intervals, since overlap_fraction divides by the shorter span and each
    half sits fully inside the parent at a flat 1.0. A split rally therefore
    behaves exactly as it would had the detector proposed both intervals.
    `winner` follows the same inheritance, with the same consequence the
    other flags don't carry: a split of an already-scored point counts
    twice in the score replay (both halves carry `point`/`winner`) until
    the reviewer clears one half.

    clip_path is the one exception, for the same reason _carried_clip_path
    demands an exact span match rather than an overlap: the file on disk was
    cut at the old span and describes neither half. Both are nulled; the
    orphaned file is `clips prune`'s job.
    """
    row = conn.execute("SELECT * FROM rallies WHERE id = ?", (rally_id,)).fetchone()
    if row is None:
        raise ValueError(f"No such rally: {rally_id}")
    if not row["start_ms"] < at_ms < row["end_ms"]:
        raise ValueError(
            f"Cut at {at_ms}ms is not strictly inside rally "
            f"{row['start_ms']}-{row['end_ms']}ms"
        )

    new_id = uuid.uuid4().hex
    try:
        # A per-row-unique negative placeholder, the same technique
        # replace_rallies uses: the real idx cannot be assigned until
        # _renumber runs, and any positive value here risks colliding with a
        # live row under UNIQUE(session_id, idx).
        conn.execute(
            "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
            "det_start_ms,det_end_ms,confidence,starred,rejected,point,winner,"
            "reviewed_at,clip_path,note,seen_at)"
            " VALUES (?,?,?,?,?,?,NULL,NULL,?,?,?,?,?,?,NULL,?,?)",
            (new_id, row["session_id"], row["source_id"], -1, at_ms, row["end_ms"],
             row["confidence"], row["starred"], row["rejected"], row["point"], row["winner"],
             row["reviewed_at"], row["note"], row["seen_at"]),
        )
        conn.execute(
            "UPDATE rallies SET end_ms = ?, clip_path = NULL WHERE id = ?",
            (at_ms, rally_id),
        )
        _renumber(conn, row["session_id"])
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return new_id


def create_rally(
    conn: sqlite3.Connection,
    source_id: str,
    start_ms: int,
    end_ms: int,
) -> str:
    """Add one rally at a span the detector never proposed. Returns its id.

    The detector cannot propose what it never scored -- pair mode hard-zeroes
    any frame missing a player -- so play it missed is otherwise unreachable:
    split only cuts an existing rally in two, and merge only undoes that.

    det_start_ms/det_end_ms are NULL, and that absence is the entire marker
    for "made by a human", exactly as it is for split_rally's second half.
    Both columns together, never one: the CHECK constraint from migration 009
    enforces it, because a half-present det span is a third state no consumer
    knows how to read. No boolean beside it, for the reason 009 gives -- a
    boolean drifts out of agreement with the columns it describes.

    Every documented consequence of that absence is inherited rather than
    re-decided here: merge_into_previous accepts this row, /label and
    /label/retract refuse it, LabelController filters it out of index/total,
    editedBoundaryCount excludes it in favour of splitCount, and a re-segment
    destroys it like any other manual edit.

    The row starts plain -- not starred, not a point, not rejected, no
    winner, no note, no clip_path. A default asserting any of those would be
    a claim nobody made, and flagging it is the same keystrokes as any other
    rally. `confidence` is 0.0 only because the column is NOT NULL: there was
    no detector run, so there is no score, and nothing may read it as one.
    det_* being NULL is what tells a reader that apart.

    Overlap with an existing rally is deliberately allowed and deliberately
    unchecked. The rally list is not a partition: clip_relpath() is
    span-derived, so two overlapping rallies name two different files, and
    score replay orders by idx, which _renumber assigns regardless. Refusing
    an overlap would also make the obvious correction -- add the span you
    meant, then reject the detector's -- impossible in that order.

    Raises ValueError on an unknown source or an incoherent span; the API
    layer turns that into a 400, as api_split already does.
    """
    src = conn.execute(
        "SELECT session_id, duration_ms FROM sources WHERE id = ?", (source_id,)
    ).fetchone()
    if src is None:
        raise ValueError(f"No such source: {source_id}")
    if end_ms - start_ms < MIN_RALLY_MS:
        raise ValueError(
            f"A rally must be at least {MIN_RALLY_MS}ms long; "
            f"{start_ms}-{end_ms}ms is {end_ms - start_ms}ms"
        )
    # end_ms may equal the duration: a span's end is exclusive, so a rally
    # running to the last frame of the file is ordinary rather than an
    # overrun. Both bounds are checked, not just the end -- the scrub bar
    # hands back a fraction of the source and a negative start is what a
    # rounding slip off the left edge produces.
    if start_ms < 0 or end_ms > src["duration_ms"]:
        raise ValueError(
            f"Span {start_ms}-{end_ms}ms falls outside the source's "
            f"0-{src['duration_ms']}ms"
        )

    new_id = uuid.uuid4().hex
    try:
        # A negative placeholder idx, the same technique split_rally and
        # replace_rallies use: the real idx cannot be assigned until
        # _renumber runs, and any positive value here risks colliding with a
        # live row under UNIQUE(session_id, idx).
        conn.execute(
            "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
            "det_start_ms,det_end_ms,confidence,starred,rejected,point,winner,"
            "reviewed_at,clip_path,note,seen_at)"
            " VALUES (?,?,?,-1,?,?,NULL,NULL,0.0,0,0,0,'',NULL,NULL,'',NULL)",
            (new_id, src["session_id"], source_id, start_ms, end_ms),
        )
        _renumber(conn, src["session_id"])
    except Exception:
        # One transaction, like split_rally: a half-applied create -- row
        # inserted, renumber never run -- would sit on the shared connection
        # violating UNIQUE(session_id, idx) until some unrelated later commit
        # persisted it.
        conn.rollback()
        raise
    conn.commit()
    return new_id


def merge_into_previous(conn: sqlite3.Connection, rally_id: str) -> None:
    """Undo a split: absorb `rally_id` into the rally that abuts it.

    Refuses unless the target carries NO detector span. That guard is the
    whole safety story -- merge can only ever undo something a human made in
    this session, and can never delete a row rally_labels is anchored to.
    It is also why the inverse of split is not "merge any two adjacent
    rallies": that more useful-sounding operation would let one keystroke
    destroy detector provenance, and fixing detector OVER-segmentation is a
    different feature with a different risk profile.

    The predecessor must abut exactly. Once the reviewer has trimmed the
    seam, the two rows no longer describe one contiguous stretch of footage
    and rejoining them would invent play across the gap they opened.

    The survivor's det_* is untouched -- merging back does not restore
    provenance to a rally that never lost it, nor invent it for one that
    never had it. Its clip_path is nulled for the same reason split_rally
    nulls it: the row's span just changed. The target's flags are discarded
    rather than merged, since split_rally made them inherited copies of the
    survivor's own.
    """
    row = conn.execute("SELECT * FROM rallies WHERE id = ?", (rally_id,)).fetchone()
    if row is None:
        raise ValueError(f"No such rally: {rally_id}")
    if row["det_start_ms"] is not None:
        raise ValueError(
            f"Rally {rally_id} carries a detector span; only a hand-made "
            f"rally can be merged back"
        )
    # ORDER BY ... LIMIT 1 makes this a total order, the same problem
    # _carried_note's third tie-break exists for above: BoundsBody validates
    # only end_ms > start_ms, so two manual drags can leave two live rallies
    # in one source sharing an end_ms, and without an explicit order
    # sqlite's pick between them is arbitrary. That matters here specifically
    # because web/src/lib/split.ts::applyMerge takes the array-adjacent rally
    # first, and only then checks whether it abuts; the precedent check
    # guarantees that when abutment succeeds, the predecessor is necessarily
    # the latest-starting row sharing that end_ms. An arbitrary server pick
    # can still merge into a DIFFERENT row than the one the reviewer's screen
    # showed absorbing the split, leaving the optimistic UI update diverged
    # from what the database actually did.
    prev = conn.execute(
        "SELECT id FROM rallies WHERE source_id = ? AND end_ms = ? AND id != ?"
        " ORDER BY start_ms DESC LIMIT 1",
        (row["source_id"], row["start_ms"], rally_id),
    ).fetchone()
    if prev is None:
        raise ValueError(
            f"Rally {rally_id} has no rally abutting its start in the same source"
        )

    try:
        conn.execute(
            "UPDATE rallies SET end_ms = ?, clip_path = NULL WHERE id = ?",
            (row["end_ms"], prev["id"]),
        )
        conn.execute("DELETE FROM rallies WHERE id = ?", (rally_id,))
        _renumber(conn, row["session_id"])
    except Exception:
        conn.rollback()
        raise
    conn.commit()


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


def list_rallies_for_source(conn: sqlite3.Connection, source_id: str) -> list[sqlite3.Row]:
    """Every rally on one source, in idx order.

    `list_rallies` is scoped to a session, which spans sources; the label
    sampler needs one source's own timeline, because a window it draws is a
    span of that source's proxy and nothing else.
    """
    return conn.execute(
        "SELECT * FROM rallies WHERE source_id = ? ORDER BY idx", (source_id,)
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

    Unmarking clears `winner`: a winner on a non-point is a contradiction
    the scoreboard would have to guess about.
    """
    now = _now()
    conn.execute(
        "UPDATE rallies SET point = ?, winner = CASE WHEN ? THEN winner ELSE '' END,"
        " reviewed_at = COALESCE(reviewed_at, ?), seen_at = COALESCE(seen_at, ?) WHERE id = ?",
        (int(point), int(point), now, now, rally_id),
    )
    conn.commit()


def set_winner(conn: sqlite3.Connection, rally_id: str, winner: str) -> None:
    """Record who won this point ('a' / 'b'), or '' to say nobody has said.

    A non-empty winner also marks the rally a point: pressing A on a rally
    is a ruling that a point was played and who took it, and making the
    reviewer press P first would be two keys for one judgement. Clearing
    the winner leaves `point` alone -- "a point was played" still stands,
    only "who won" is withdrawn. Stamps reviewed_at/seen_at like set_point:
    this is a ruling on the clip.
    """
    if winner not in ("", "a", "b"):
        raise ValueError(f"winner must be '', 'a' or 'b', not {winner!r}")
    now = _now()
    conn.execute(
        "UPDATE rallies SET winner = ?, point = CASE WHEN ? != '' THEN 1 ELSE point END,"
        " reviewed_at = COALESCE(reviewed_at, ?), seen_at = COALESCE(seen_at, ?) WHERE id = ?",
        (winner, winner, now, now, rally_id),
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
