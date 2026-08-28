"""Applying a setup decision: rotation plus play region, then a rebuild.

One function, called by both the API route and the CLI, so the two cannot
drift on validation -- an invalid rotation must be rejected identically
whether it arrives over HTTP or from a terminal.
"""

import sqlite3

from splitstep.db import jobs as jobq
from splitstep.db.presets import get_preset
from splitstep.db.sessions import get_source, set_source_setup
from splitstep.media.transcode import rotation_filter

# A source whose proxy is being written, or whose features are being
# extracted, cannot have either input changed underneath the running job.
BUSY_STATUSES = {"ingesting", "building", "detecting"}


def queue_setup(
    conn: sqlite3.Connection, source_id: str, rotation_deg: int, preset_id: str
) -> str:
    # Validate everything before mutating anything, in strict order:
    # 1. Caller's own inputs (syntactic): source exists, source not busy, rotation legal
    # 2. Database lookups: preset exists
    # This ensures a bad rotation is rejected before a DB lookup masks it.
    source = get_source(conn, source_id)
    if source is None:
        raise LookupError(f"No such source: {source_id}")
    if source["status"] in BUSY_STATUSES:
        raise RuntimeError(f"source is {source['status']}; wait for that job to finish")
    # Call rotation_filter for its exception (validation), discarding the return value.
    # rotation_filter is the single source of truth; it is also called by set_source_setup,
    # so using it here doesn't duplicate the rule.
    rotation_filter(rotation_deg)
    if get_preset(conn, preset_id) is None:
        raise LookupError(f"No such preset: {preset_id}")

    # All validation passed. Write both columns in one statement.
    set_source_setup(conn, source_id, rotation_deg, preset_id)
    return jobq.enqueue(conn, "build_proxy", {"source_id": source_id})
