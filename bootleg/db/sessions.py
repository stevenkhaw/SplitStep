import sqlite3
import uuid
from datetime import UTC, datetime

from bootleg.media.transcode import rotation_filter


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


def find_source_by_original_name(
    conn: sqlite3.Connection, original_name: str
) -> sqlite3.Row | None:
    """Find a source row by original_name alone, with no session_id to
    scope the search.

    Used when a requeued ingest job finds its inbox path already gone: the
    payload it was handed carries only a file path, not the session the
    earlier attempt filed it under, so get_source_by_original_name's
    session-scoped lookup isn't available. Callers use this to tell "the
    earlier attempt already finished, reaffirm its status" apart from "this
    file never existed at all".
    """
    return conn.execute(
        "SELECT * FROM sources WHERE original_name = ? ORDER BY idx LIMIT 1",
        (original_name,),
    ).fetchone()


def set_source_status(conn: sqlite3.Connection, source_id: str, status: str) -> None:
    conn.execute("UPDATE sources SET status = ? WHERE id = ?", (status, source_id))
    conn.commit()


def set_session_status(conn: sqlite3.Connection, session_id: str, status: str) -> None:
    conn.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))
    conn.commit()


def refresh_session_review_status(conn: sqlite3.Connection, session_id: str) -> str:
    """Flip a session to 'reviewed' once no rally in it is unseen, without
    disturbing a status this pass does not own.

    Spec 6: every exit path from a rally sets reviewed_at -- starring,
    rejecting, skipping, and auto-advance all count as seen.

    This is one guarded UPDATE, not a read-then-write. Every HTTP request
    runs on its own thread with its own connection (see
    ThreadLocalConnections in bootleg/api/app.py), so two review actions on
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
                                   WHERE session_id = :sid AND reviewed_at IS NULL)
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
