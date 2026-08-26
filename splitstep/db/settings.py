"""Per-library key/value settings (migration 011).

One table, JSON values where a value has structure. The clip colour profile
lives here rather than in code because it is a fact about the library's
existing clips -- the thing `-c copy` concat has to stay compatible with --
not a fact about the app.
"""

import json
import sqlite3

CLIP_COLOR_PROFILE_KEY = "clip_color_profile"


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def get_color_profile(conn: sqlite3.Connection) -> tuple[str, str, str, str] | None:
    raw = get_setting(conn, CLIP_COLOR_PROFILE_KEY)
    if raw is None:
        return None
    values = json.loads(raw)
    return (values[0], values[1], values[2], values[3])


def lock_color_profile(conn: sqlite3.Connection, profile: tuple[str, str, str, str]) -> None:
    set_setting(conn, CLIP_COLOR_PROFILE_KEY, json.dumps(list(profile)))
