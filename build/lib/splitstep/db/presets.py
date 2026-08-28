import sqlite3
import uuid
from datetime import UTC, datetime

from splitstep.detect.geometry import Quad


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create_preset(conn: sqlite3.Connection, name: str, quad: Quad) -> str:
    """Store a validated play-region quad under a name, reusable across every
    session shot from the same camera position. See spec Stage 0.5.
    """
    preset_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO court_presets (id,name,quad,created_at) VALUES (?,?,?,?)",
        (preset_id, name, quad.to_json(), _now()),
    )
    conn.commit()
    return preset_id


def list_presets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM court_presets ORDER BY created_at"
    ).fetchall()


def get_preset(conn: sqlite3.Connection, preset_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM court_presets WHERE id = ?", (preset_id,)
    ).fetchone()
