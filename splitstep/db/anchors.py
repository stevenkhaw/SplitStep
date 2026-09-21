"""Score anchors (migration 017): a reviewer's "entering here, the score
is X", stored as an input to the replay and keyed on a time on a source.

Readers hand the list to splitstep.score.score_before, which is the only
thing that decides which rally an anchor lands on -- the table knows a
time, not a rally, on purpose (see the migration's comment).
"""

import json
import sqlite3
from datetime import UTC, datetime

from splitstep.score import Anchor, AnchorState, anchor_from_dict, anchor_to_dict


def _now() -> str:
    return datetime.now(UTC).isoformat()


def set_anchor(conn: sqlite3.Connection, source_id: str, at_ms: int, state: AnchorState) -> None:
    """Write the anchor at `at_ms`, replacing one already there. Replace
    rather than refuse: two anchors at one instant would be two claims
    about the same moment, and the newer one is the correction."""
    conn.execute(
        "INSERT INTO score_anchors (source_id, at_ms, state, created_at) VALUES (?,?,?,?)"
        " ON CONFLICT(source_id, at_ms) DO UPDATE SET state = excluded.state,"
        " created_at = excluded.created_at",
        (source_id, at_ms, json.dumps(anchor_to_dict(state)), _now()),
    )
    conn.commit()


def delete_anchor(conn: sqlite3.Connection, source_id: str, at_ms: int) -> bool:
    """Remove the anchor at `at_ms`. False when there was none, so the
    route can 404 instead of claiming a removal that did not happen."""
    cur = conn.execute(
        "DELETE FROM score_anchors WHERE source_id = ? AND at_ms = ?", (source_id, at_ms)
    )
    conn.commit()
    return cur.rowcount > 0


def list_anchors(conn: sqlite3.Connection, session_id: str) -> list[Anchor]:
    """Every anchor on every source in the session, in source then time
    order. Session-scoped like list_rallies, because that is the list
    score_before replays over -- an anchor on one source can carry into
    the next source's points."""
    rows = conn.execute(
        "SELECT a.source_id, a.at_ms, a.state FROM score_anchors a"
        " JOIN sources s ON s.id = a.source_id"
        " WHERE s.session_id = ? ORDER BY s.idx, a.at_ms",
        (session_id,),
    ).fetchall()
    return [Anchor(r["source_id"], r["at_ms"], anchor_from_dict(json.loads(r["state"])))
            for r in rows]


def anchor_json(a: Anchor) -> dict:
    """The wire shape: the state flattened beside where it sits, which is
    what web/src/lib/types.ts's ScoreAnchor declares."""
    return {"source_id": a.source_id, "at_ms": a.at_ms, **anchor_to_dict(a.state)}
