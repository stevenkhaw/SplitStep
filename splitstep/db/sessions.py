import sqlite3
import uuid
from datetime import UTC, datetime

from splitstep.media.transcode import rotation_filter


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _check_rotation(rotation_deg: int) -> int:
    # rotation_filter is the single source of truth for what is legal; a
    # CHECK constraint would surface a bad value as an opaque IntegrityError
    # from three layers down instead of a message naming the four options.
    rotation_filter(rotation_deg)
    return rotation_deg


def create_session(conn: sqlite3.Connection, session_id: str,
                   title: str, played_on: str) -> str:
    conn.execute(
        "INSERT INTO sessions (id,title,played_on,status,created_at)"
        " VALUES (?,?,?,'ingesting',?)",
        (session_id, title, played_on, _now()),
    )
    conn.commit()
    return session_id


def find_or_create_session_for_date(conn: sqlite3.Connection, played_on: str) -> str:
    row = conn.execute(
        "SELECT id FROM sessions WHERE played_on = ? ORDER BY id LIMIT 1",
        (played_on,),
    ).fetchone()
    if row:
        return row["id"]
    return create_session(conn, played_on, played_on, played_on)


def add_source(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    recorded_at: str,
    duration_ms: int,
    width: int,
    height: int,
    fps: float,
    original_name: str | None,
    rotation_deg: int = 0,
) -> tuple[str, int]:
    row = conn.execute(
        "SELECT COALESCE(MAX(idx),0) AS max_idx,"
        " COALESCE(SUM(duration_ms),0) AS total FROM sources WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    idx = row["max_idx"] + 1
    offset_ms = row["total"]
    source_id = uuid.uuid4().hex

    conn.execute(
        "INSERT INTO sources (id,session_id,idx,recorded_at,offset_ms,duration_ms,"
        "width,height,fps,original_name,rotation_deg,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,'ingesting')",
        (source_id, session_id, idx, recorded_at, offset_ms, duration_ms,
         width, height, fps, original_name, _check_rotation(rotation_deg)),
    )
    conn.commit()
    return source_id, idx


def get_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()


def list_sessions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sessions ORDER BY played_on DESC, id DESC"
    ).fetchall()


def list_sources(conn: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sources WHERE session_id = ? ORDER BY idx", (session_id,)
    ).fetchall()


def get_source(conn: sqlite3.Connection, source_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()


def get_source_by_original_name(
    conn: sqlite3.Connection, session_id: str, original_name: str
) -> sqlite3.Row | None:
    """Find an existing source row for a file already ingested into this
    session, so a retried ingest reuses it instead of adding a duplicate.
    """
    return conn.execute(
        "SELECT * FROM sources WHERE session_id = ? AND original_name = ?"
        " ORDER BY idx LIMIT 1",
        (session_id, original_name),
    ).fetchone()


def find_ingesting_sources_by_original_name(
    conn: sqlite3.Connection, original_name: str
) -> list[sqlite3.Row]:
    """Find every source row named original_name that is still stuck at
    status='ingesting', with no session_id to scope the search.

    This is the set a requeued ingest job checks first when its inbox path
    is already gone: status, not name, is what identifies a crashed job.
    Two sessions can hold a source with the same original_name (a phone
    reusing IMG_0001.MOV across days), but only a source stranded by a
    crash between the move and the status write is still sitting at
    'ingesting' -- a source that finished ingest is already at
    'needs_setup' or beyond. A single match here is unambiguous; more than
    one means two crashes raced on the same name and neither can be
    trusted over the other.
    """
    return conn.execute(
        "SELECT * FROM sources WHERE original_name = ? AND status = 'ingesting'"
        " ORDER BY idx",
        (original_name,),
    ).fetchall()


def find_sources_by_original_name(
    conn: sqlite3.Connection, original_name: str
) -> list[sqlite3.Row]:
    """Find every source row named original_name, with no session_id or
    status to scope the search.

    Used only to answer a set-membership question -- "has ANY attempt at
    this name ever completed its move" -- never to pick one row to act on.
    A name-only match can span sessions (a phone reusing IMG_0001.MOV
    across days), so treating any single row here as *the* row would be an
    arbitrary tiebreak. See find_ingesting_sources_by_original_name for the
    narrower, single-row-safe set that crash recovery actually acts on.
    """
    return conn.execute(
        "SELECT * FROM sources WHERE original_name = ? ORDER BY idx",
        (original_name,),
    ).fetchall()


def set_source_status(conn: sqlite3.Connection, source_id: str, status: str) -> None:
    conn.execute("UPDATE sources SET status = ? WHERE id = ?", (status, source_id))
    conn.commit()


def set_session_status(conn: sqlite3.Connection, session_id: str, status: str) -> None:
    conn.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))
    conn.commit()


def refresh_session_review_status(conn: sqlite3.Connection, session_id: str) -> str:
    """Flip a session to 'reviewed' once every rally in it has been ruled on,
    without disturbing a status this pass does not own.

    Spec 6 counted every exit path from a rally as seen -- starring,
    rejecting, skipping, auto-advance. Review-UX 2.4 then narrowed it,
    because `->` is pressed on every clip merely to walk the pass and
    stamping `reviewed_at` there would call a session reviewed with no
    judgement in it. Correct, and still true: only a ruling stamps
    reviewed_at (set_star/set_point/set_rejected, each through the same
    COALESCE so the first wins; set_note and labelling abstain).

    What that narrowing had no way to express, with one column doing both
    jobs, is that walking a pass end to end IS finishing it. Migration 008
    split the column, and this now reads `seen_at`: a session is done when
    it has been looked through, whether or not anything in it was worth
    starring. Superseding 2.4's session-status half is deliberate
    (2026-08-22); its reviewed_at half stands untouched.

    This is one guarded UPDATE, not a read-then-write. Every HTTP request
    runs on its own thread with its own connection (see
    ThreadLocalConnections in splitstep/api/app.py), so two review actions on
    the last two rallies in a session -- the normal way every session ends
    -- can land on separate connections at the same instant. A separate
    SELECT-then-UPDATE could let both read a stale "still unseen" count and
    both write 'ready'; once every rally is reviewed there is nothing left
    to click, so nothing would ever retrigger the refresh and the session
    would never reach 'reviewed'. Computing the verdict inside the UPDATE's
    own CASE makes SQLite evaluate and apply it as a single statement,
    closing that window.

    The `status IN ('ready', 'reviewed')` guard confines this to the review
    pass. Rally rows are addressable before a session reaches 'ready' --
    handlers.py calls replace_rallies before marking a source ready, and a
    multi-source session stays 'detecting' while a sibling source is still
    processing -- and a session can also be 'failed'. A review action must
    not silently overwrite any of those with 'ready'/'reviewed'; that
    belongs to the job pipeline alone.

    A session with zero rallies stays 'ready' forever rather than becoming
    'reviewed': detection finding nothing means the threshold needs
    lowering, not that review is done.
    """
    conn.execute(
        """
        UPDATE sessions
           SET status = CASE
                 WHEN (SELECT COUNT(*) FROM rallies WHERE session_id = :sid) > 0
                  AND NOT EXISTS (SELECT 1 FROM rallies
                                   WHERE session_id = :sid AND seen_at IS NULL)
                 THEN 'reviewed' ELSE 'ready' END
         WHERE id = :sid
           AND status IN ('ready', 'reviewed')
        """,
        {"sid": session_id},
    )
    conn.commit()
    return conn.execute(
        "SELECT status FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()["status"]


def set_source_preset(conn: sqlite3.Connection, source_id: str, preset_id: str) -> None:
    """Assign a court preset to a source, so the next `detect` uses it
    instead of `DEFAULT_QUAD` (the whole frame) -- see handlers._quad_for.
    """
    conn.execute(
        "UPDATE sources SET court_preset_id = ? WHERE id = ?", (preset_id, source_id)
    )
    conn.commit()


def set_source_rotation(conn: sqlite3.Connection, source_id: str, rotation_deg: int) -> None:
    conn.execute(
        "UPDATE sources SET rotation_deg=? WHERE id=?",
        (_check_rotation(rotation_deg), source_id),
    )
    conn.commit()


def set_source_dimensions(conn: sqlite3.Connection, source_id: str, width: int, height: int) -> None:
    """Record the ACTUAL dimensions of a source's proxy file.

    add_source seeds width/height once, at ingest time, from the original's
    probed rotation. Neither set_source_setup (rotation+preset only) nor
    handle_build_proxy's own status write ever touch them again, so a
    source seeded at one rotation and later corrected by the wizard keeps
    reporting its stale ingest-time pair even though the proxy build
    changed its actual shape -- exactly the mismatch `splitstep doctor`
    prints as its headline diagnostic. Call this with dimensions probed
    from the proxy itself, not recomputed from rotation_deg: the proxy is
    the artifact everything downstream (the player, doctor, this row)
    actually reads.
    """
    conn.execute(
        "UPDATE sources SET width=?, height=? WHERE id=?",
        (width, height, source_id),
    )
    conn.commit()


def set_source_setup(
    conn: sqlite3.Connection, source_id: str, rotation_deg: int, preset_id: str
) -> None:
    """Update rotation and preset in a single write, validating rotation first.

    If validation fails, no columns are written. If the write succeeds but a
    caller later fails to enqueue the job, the source stays updated: re-running
    setup detects and recovers that window (the job is idempotent).
    """
    conn.execute(
        "UPDATE sources SET rotation_deg = ?, court_preset_id = ? WHERE id = ?",
        (_check_rotation(rotation_deg), preset_id, source_id),
    )
    conn.commit()
