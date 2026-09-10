"""Per-library key/value settings (migration 011).

One table, JSON values where a value has structure. The clip colour profile
lives here rather than in code because it is a fact about the library's
existing clips -- the thing `-c copy` concat has to stay compatible with --
not a fact about the app.
"""

import json
import sqlite3
from pathlib import Path

CLIP_COLOR_PROFILE_KEY = "clip_color_profile"
# The RallyMetrics `clips/` folder: copies of this library's clips with a
# heart-rate overlay burned in, named `<session_id>/<idx>/<start>-<end>.mp4`
# to mirror clip_relpath exactly. A library setting rather than app config
# because it pairs with THIS library's clips.
HR_CLIPS_ROOT_KEY = "hr_clips_root"


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


def get_hr_clips_root(conn: sqlite3.Connection) -> Path | None:
    raw = get_setting(conn, HR_CLIPS_ROOT_KEY)
    return Path(raw) if raw else None


def set_hr_clips_root(conn: sqlite3.Connection, root: Path | None) -> None:
    if root is None:
        conn.execute("DELETE FROM settings WHERE key=?", (HR_CLIPS_ROOT_KEY,))
        conn.commit()
        return
    set_setting(conn, HR_CLIPS_ROOT_KEY, str(root))
