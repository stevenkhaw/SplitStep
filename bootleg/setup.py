"""Applying a setup decision: rotation plus play region, then a rebuild.

One function, called by both the API route and the CLI, so the two cannot
drift on validation -- an invalid rotation must be rejected identically
whether it arrives over HTTP or from a terminal.
"""

import sqlite3

from bootleg.db import jobs as jobq
from bootleg.db.presets import get_preset
from bootleg.db.sessions import get_source, set_source_preset, set_source_rotation

# A source whose proxy is being written, or whose features are being
# extracted, cannot have either input changed underneath the running job.
BUSY_STATUSES = {"ingesting", "building", "detecting"}


def queue_setup(
    conn: sqlite3.Connection, source_id: str, rotation_deg: int, preset_id: str
) -> str:
    source = get_source(conn, source_id)
    if source is None:
        raise LookupError(f"No such source: {source_id}")
    if source["status"] in BUSY_STATUSES:
        raise RuntimeError(f"source is {source['status']}; wait for that job to finish")
    if get_preset(conn, preset_id) is None:
        raise LookupError(f"No such preset: {preset_id}")

    set_source_rotation(conn, source_id, rotation_deg)  # raises ValueError on a bad angle
    set_source_preset(conn, source_id, preset_id)
    return jobq.enqueue(conn, "build_proxy", {"source_id": source_id})
