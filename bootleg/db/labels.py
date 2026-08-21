import sqlite3
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

VERDICTS = ("clean", "not_play", "partly", "unsure")

# Fixed order, not the caller's. An exported fixture is committed to
# tests/fixtures and re-exported later; a set-ordered join would produce diff
# noise on every export for no change in content. Same reasoning as
# features.jsonl quantizing its floats to 4dp for byte-stable round trips.
FLAG_ORDER = ("start_early", "start_late", "end_early", "end_late")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def format_flags(flags: Iterable[str]) -> str:
    present = set(flags)
    unknown = present - set(FLAG_ORDER)
    if unknown:
        raise ValueError(f"unknown boundary flag(s): {sorted(unknown)}")
    return ",".join(f for f in FLAG_ORDER if f in present)


def parse_flags(raw: str) -> list[str]:
    # "".split(",") is [""], not [] -- filter, do not rstrip-and-split.
    return [f for f in raw.split(",") if f]


def add_label(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    span_start_ms: int,
    span_end_ms: int,
    verdict: str | None = None,
    boundary_flags: Iterable[str] = (),
    true_start_ms: int | None = None,
    true_end_ms: int | None = None,
    rally_id: str | None = None,
    retracted: bool = False,
) -> str:
    """Append one human judgement about a span of a source.

    `span_start_ms`/`span_end_ms` must be the detector's own guess
    (`det_start_ms`/`det_end_ms`), never the human-edited bounds -- that is
    what keeps a label meaningful across re-segments.

    Never updates. Re-labelling the same span appends another row and
    `latest_labels` resolves which one is current, so a corrected judgement
    never erases the one it corrected.

    Deliberately dumb: this function does not look up what a previous row for
    the same span already said, so a caller that always passes verdict=None
    (or always true_start_ms=None) will silently blank that field out of the
    latest row every time it runs. Carrying the other half of a label forward
    is the caller's job -- see `record_boundary_correction` below and
    `api_label` in `bootleg/api/routes.py`, which is why `latest_label_for_span`
    exists.
    """
    if verdict is not None and verdict not in VERDICTS:
        raise ValueError(f"unknown verdict: {verdict!r}")
    # Also enforced by a CHECK in migration 004; raised here too so the caller
    # bug reads as one rather than as a database integrity error three frames
    # away from the mistake.
    if retracted and verdict is not None:
        raise ValueError("a retracted row cannot carry a verdict")

    label_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO rally_labels (id,source_id,span_start_ms,span_end_ms,verdict,"
        "boundary_flags,true_start_ms,true_end_ms,rally_id,labelled_at,retracted)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (label_id, source_id, span_start_ms, span_end_ms, verdict,
         format_flags(boundary_flags), true_start_ms, true_end_ms, rally_id, _now(),
         1 if retracted else 0),
    )
    conn.commit()
    return label_id


# Shared by latest_labels and latest_label_for_span so the "newest row per
# span" window function has one definition. The tie-break on `rowid DESC` is
# not decoration: two labels written inside the same clock tick share a
# `labelled_at`, and without it sqlite would be free to return either. Higher
# rowid is the later INSERT, which is the later judgement.
_LATEST_PER_SPAN = (
    "SELECT *, ROW_NUMBER() OVER ("
    "         PARTITION BY span_start_ms, span_end_ms"
    "         ORDER BY labelled_at DESC, rowid DESC) AS rn"
    "  FROM rally_labels WHERE source_id = ?"
)


def latest_labels(conn: sqlite3.Connection, source_id: str) -> list[sqlite3.Row]:
    """The current judgement for each distinct span of a source.

    Resolution is two steps, and the second one is not decoration: take the
    newest row per span (`_LATEST_PER_SPAN`), then drop it if what it leaves
    behind says nothing -- no verdict and no corrected span. Only a retraction
    can be in that state (the CHECK rejects every other route to it), and a
    span whose verdict has been withdrawn is exactly as unjudged as one nobody
    ever opened. Dropping it here is what lets every reader -- label mode's
    seed, `cmd_labels_export`, the scorer -- stay ignorant of retractions
    entirely: they simply never see the span.

    A retraction that DOES leave something behind -- a boundary correction
    carried forward from a drag -- is returned, and looks to a reader exactly
    like the drag-only row it effectively is: verdict NULL, true_* set. That
    is the honest answer. Undo in label mode retracts the reviewer's
    judgement; it was never a claim about the millisecond measurement another
    writer took.

    Contrast `latest_label_for_span`, which deliberately does NOT filter --
    see its docstring.
    """
    return conn.execute(
        f"SELECT * FROM ({_LATEST_PER_SPAN}) WHERE rn = 1"
        "   AND (verdict IS NOT NULL OR true_start_ms IS NOT NULL)"
        " ORDER BY span_start_ms",
        (source_id,),
    ).fetchall()


def latest_label_for_span(
    conn: sqlite3.Connection, source_id: str, span_start_ms: int, span_end_ms: int
) -> sqlite3.Row | None:
    """The current latest row for one exact span, or None if never labelled.

    Exists so a writer can carry the other half of a label forward onto its
    own new row -- see `record_boundary_correction` and `api_label` in
    `bootleg/api/routes.py`. Without this lookup, whichever of the two
    writers runs second has no way to know a row already exists for this
    span, and its own row (which always leaves the other writer's field NULL)
    would be the only one `latest_labels` ever returns.

    Unlike `latest_labels` this returns the newest row RAW, retractions
    included. A writer must see one: `record_boundary_correction` carries the
    prior verdict forward, and if a retraction were filtered out here it would
    read past it to the verdict that retraction withdrew and write it onto its
    own row -- undoing the reviewer's undo, invisibly, on the next drag.
    """
    return conn.execute(
        f"SELECT * FROM ({_LATEST_PER_SPAN}) WHERE rn = 1"
        " AND span_start_ms = ? AND span_end_ms = ?",
        (source_id, span_start_ms, span_end_ms),
    ).fetchone()


def derive_boundary_flags(
    det_start_ms: int, det_end_ms: int, true_start_ms: int, true_end_ms: int
) -> list[str]:
    """Which edges moved which way, as a pure function of the four ms values.

    Once a measured correction exists there is exactly one right answer for
    boundary_flags -- it is the sign of true_* vs det_*, not an opinion. Two
    callers need this: `record_boundary_correction` derives it for the row a
    drag writes, and `api_label` recomputes it when carrying a correction
    forward onto a verdict row rather than trusting a client-supplied list
    (Finding 1, docs/superpowers/specs/2026-08-21-rally-labelling-design.md
    -- the reviewer cannot have seen these flags to knowingly clear them,
    since LabelController never surfaces a verdict-NULL row). One definition
    here instead of two copies is what keeps "which edge moved which way"
    from drifting between the two call sites.
    """
    flags: list[str] = []
    # Detector opened before play began -> it started early, and vice versa.
    if true_start_ms > det_start_ms:
        flags.append("start_early")
    elif true_start_ms < det_start_ms:
        flags.append("start_late")
    # Detector ran on past the end -> it ended late, and vice versa.
    if true_end_ms < det_end_ms:
        flags.append("end_late")
    elif true_end_ms > det_end_ms:
        flags.append("end_early")
    return flags


def record_boundary_correction(
    conn: sqlite3.Connection,
    *,
    rally_id: str,
    source_id: str,
    det_start_ms: int,
    det_end_ms: int,
    true_start_ms: int,
    true_end_ms: int,
) -> str | None:
    """Turn a manual boundary drag into a boundary-bearing label.

    Returns the new label id, or None when the saved span equals the
    detector's -- a drag that went nowhere is not a correction.

    This call never asserts a verdict of its own -- dragging the handles
    supplies corrected edges, not a judgement that the span contains a rally,
    and defaulting to 'clean' would fabricate one the reviewer never made.
    But if a verdict was already recorded for this exact span (label mode ran
    first), it carries that verdict forward onto this new row rather than
    leaving it NULL. Without this, this row -- append-only, so it becomes the
    one `latest_labels` returns -- would silently erase the earlier verdict
    from every reader (H1, docs/superpowers/specs/2026-08-21-rally-labelling-
    design.md). The mirror carry-forward, for the opposite write order, lives
    in `api_label` (bootleg/api/routes.py).
    """
    if (true_start_ms, true_end_ms) == (det_start_ms, det_end_ms):
        return None

    flags = derive_boundary_flags(det_start_ms, det_end_ms, true_start_ms, true_end_ms)

    # boundary_flags is NOT carried forward the same way: it is a pure
    # function of (true_start_ms, true_end_ms, det_start_ms, det_end_ms), all
    # four of which this call already has, so it is always recomputed fresh
    # above rather than copied from `prior`. Copying it would let a stale or
    # manually-guessed flag from an earlier row outlive a measurement that
    # just proved it wrong.
    prior = latest_label_for_span(conn, source_id, det_start_ms, det_end_ms)
    verdict = prior["verdict"] if prior is not None else None

    return add_label(
        conn, source_id=source_id, span_start_ms=det_start_ms, span_end_ms=det_end_ms,
        verdict=verdict, boundary_flags=flags,
        true_start_ms=true_start_ms, true_end_ms=true_end_ms, rally_id=rally_id,
    )


def retract_label(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    span_start_ms: int,
    span_end_ms: int,
    rally_id: str | None = None,
) -> str | None:
    """Withdraw the current verdict for a span, durably and append-only.

    This is what label mode's `U` writes. Undo used to be client-only: the
    controller dropped the verdict from its own map and returned nothing to
    POST, so the reviewer was left looking at an unlabelled clip while
    `latest_labels` still returned the verdict they had just taken back, and
    a reload brought it straight back. A row that says "retracted" is the
    smallest thing that makes the two agree without ever deleting history.

    Returns the new label id, or None when the span carries no verdict to
    withdraw -- never labelled, already retracted, or holding only a drag's
    boundary correction, which is not this reviewer's judgement to retract.
    Same shape as `record_boundary_correction` returning None for a drag that
    went nowhere, and for the same reason: an append-only table should not
    collect rows that assert nothing.

    A boundary correction already on record is carried forward exactly as
    `api_label` carries one forward, flags re-derived from it rather than
    copied. Undo retracts the reviewer's *judgement*; the measurement a drag
    took is a different writer's fact and was never in question. What that
    leaves is a row indistinguishable, to a reader, from the drag-only row
    that would exist had the verdict never been given -- which is precisely
    the state being restored.
    """
    prior = latest_label_for_span(conn, source_id, span_start_ms, span_end_ms)
    if prior is None or prior["verdict"] is None:
        return None

    true_start_ms = prior["true_start_ms"]
    true_end_ms = prior["true_end_ms"]
    flags = (
        derive_boundary_flags(span_start_ms, span_end_ms, true_start_ms, true_end_ms)
        if true_start_ms is not None and true_end_ms is not None
        else []
    )

    return add_label(
        conn, source_id=source_id, span_start_ms=span_start_ms, span_end_ms=span_end_ms,
        verdict=None, boundary_flags=flags,
        true_start_ms=true_start_ms, true_end_ms=true_end_ms, rally_id=rally_id,
        retracted=True,
    )
