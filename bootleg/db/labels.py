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
) -> str:
    """Append one human judgement about a span of a source.

    `span_start_ms`/`span_end_ms` must be the detector's own guess
    (`det_start_ms`/`det_end_ms`), never the human-edited bounds -- that is
    what keeps a label meaningful across re-segments.

    Never updates. Re-labelling the same span appends another row and
    `latest_labels` resolves which one is current, so a corrected judgement
    never erases the one it corrected.
    """
    if verdict is not None and verdict not in VERDICTS:
        raise ValueError(f"unknown verdict: {verdict!r}")

    label_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO rally_labels (id,source_id,span_start_ms,span_end_ms,verdict,"
        "boundary_flags,true_start_ms,true_end_ms,rally_id,labelled_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (label_id, source_id, span_start_ms, span_end_ms, verdict,
         format_flags(boundary_flags), true_start_ms, true_end_ms, rally_id, _now()),
    )
    conn.commit()
    return label_id


def latest_labels(conn: sqlite3.Connection, source_id: str) -> list[sqlite3.Row]:
    """The current judgement for each distinct span of a source.

    The tie-break on `rowid DESC` is not decoration: two labels written inside
    the same clock tick share a `labelled_at`, and without it sqlite would be
    free to return either. Higher rowid is the later INSERT, which is the
    later judgement.
    """
    return conn.execute(
        "SELECT * FROM ("
        "  SELECT *, ROW_NUMBER() OVER ("
        "           PARTITION BY span_start_ms, span_end_ms"
        "           ORDER BY labelled_at DESC, rowid DESC) AS rn"
        "    FROM rally_labels WHERE source_id = ?"
        ") WHERE rn = 1 ORDER BY span_start_ms",
        (source_id,),
    ).fetchall()


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

    `verdict` stays NULL on purpose. Dragging the handles asserts that the
    edges were wrong and supplies the right ones; it does not assert that the
    span contains a rally, and defaulting it to 'clean' would fabricate a
    judgement the reviewer never made.
    """
    if (true_start_ms, true_end_ms) == (det_start_ms, det_end_ms):
        return None

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

    return add_label(
        conn, source_id=source_id, span_start_ms=det_start_ms, span_end_ms=det_end_ms,
        verdict=None, boundary_flags=flags,
        true_start_ms=true_start_ms, true_end_ms=true_end_ms, rally_id=rally_id,
    )
