"""Applying a setup decision: rotation plus play region, then a rebuild.

One function, called by both the API route and the CLI, so the two cannot
drift on validation -- an invalid rotation must be rejected identically
whether it arrives over HTTP or from a terminal.
"""

import json
import sqlite3
from typing import Any

from splitstep.db import jobs as jobq
from splitstep.db.presets import create_preset, get_preset
from splitstep.db.sessions import (
    MOUNTS,
    PLAY_FORMATS,
    get_source,
    set_source_choices,
    set_source_setup,
)
from splitstep.detect.court import Court, region_from_corners, validate_corners
from splitstep.detect.scorer import resolve_scorer
from splitstep.media.transcode import rotation_filter

# A source whose proxy is being written, or whose features are being
# extracted, cannot have either input changed underneath the running job.
BUSY_STATUSES = {"ingesting", "building", "detecting"}


class _Keep:
    """The default for mount, format and corners: leave what the row has.

    A sentinel rather than '' because '' is a real answer (the wizard's "Not
    set"). With '' as the default a `splitstep setup --rotation 90` re-run
    would silently wipe a choice made in the wizard; only an explicit value,
    '' and None included, writes.
    """

    def __repr__(self) -> str:
        return "KEEP"


KEEP: Any = _Keep()

FENCE_NEEDS_CORNERS = (
    "Fence + Singles needs the four court corners — draw them on the court lines."
)


def validate_choices(
    source,
    mount: Any = KEEP,
    play_format: Any = KEEP,
    court_corners: Any = KEEP,
) -> tuple[str, str, list[list[float]] | None]:
    """Resolve and validate the three choices against the source row.

    The one function queue_setup and POST /choices both call, so HTTP, CLI and
    the processed-source editor refuse the same inputs with the same sentence.
    Omitted values resolve to what the row already holds, and the fence rule
    is judged on the *resolved* trio: changing only the format to singles on a
    fence source with no corners must still be refused. Returns the values to
    write; nothing is written here.
    """
    if mount is KEEP:
        mount = source["mount"]
    elif mount and mount not in MOUNTS:
        raise ValueError(f"unknown mount {mount!r}; expected one of {MOUNTS} or ''")
    if play_format is KEEP:
        play_format = source["play_format"]
    elif play_format and play_format not in PLAY_FORMATS:
        raise ValueError(
            f"unknown play format {play_format!r}; expected one of {PLAY_FORMATS} or ''"
        )
    mount, play_format = mount or "", play_format or ""
    if court_corners is KEEP:
        stored = source["court_corners"]
        corners = json.loads(stored) if stored else None
    elif court_corners is None:
        corners = None
    else:
        corners = [list(p) for p in validate_corners(court_corners)]
    if resolve_scorer(mount, play_format) == "fence" and not corners:
        raise ValueError(FENCE_NEEDS_CORNERS)
    return mount, play_format, corners


def _forget_scorer_if_corners_moved(conn, source, corners) -> None:
    """Clear `detected_scorer` when the stored court corners really change.

    A fence source reads its corners, not the play-region preset, so a redrawn
    court changes nothing the client could compare: scorer_now stays 'fence'
    and detected_scorer stays 'fence', and the panel would call the rallies
    current over a court they were not cut under. NULL is "unknown", which
    the client's `detectionOwed` already turns into a re-cut when fence is
    wanted and boxes are cached. Compared by parsed value, never by string:
    re-saving the same four corners (the wizard does, on every save) is not a
    change and must not cost a re-cut.
    """
    stored = source["court_corners"]
    before = json.loads(stored) if stored else None
    norm = None if corners is None else [list(map(float, p)) for p in corners]
    if before is not None:
        before = [list(map(float, p)) for p in before]
    if before != norm:
        conn.execute("UPDATE sources SET detected_scorer = NULL WHERE id = ?", (source["id"],))
        conn.commit()


def queue_setup(
    conn: sqlite3.Connection,
    source_id: str,
    rotation_deg: int,
    preset_id: str | None,
    *,
    mount: Any = KEEP,
    play_format: Any = KEEP,
    court_corners: Any = KEEP,
) -> str:
    # Validate everything before mutating anything, in strict order:
    # 1. Caller's own inputs (syntactic): source exists, source not busy, rotation legal,
    #    mount/format/corners legal
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
    if rotation_deg != source["rotation_deg"] and court_corners is KEEP:
        # Corners are clicks on the proxy as it was oriented when they were
        # drawn. Re-rotating the source and keeping them leaves four points
        # that still pass every order/convexity check but sit in the old
        # frame, so the homography maps every player to the wrong place with
        # no error anywhere. Clearing them here (and judging the choices on
        # the cleared value) makes a fence+singles source refuse with the
        # needs-corners sentence until they are redrawn. An explicit value
        # in the same call wins: the wizard sends rotation and fresh corners
        # together.
        court_corners = None
    mount, play_format, corners = validate_choices(source, mount, play_format, court_corners)
    derived_quad = None
    if preset_id is None:
        # The server derives the play region from the corners the caller just
        # gave, so the wizard sends one set of corners rather than corners
        # plus a region that could disagree with them.
        if court_corners is KEEP or court_corners is None:
            raise ValueError("a play region is required: pass a preset or the court corners")
        derived_quad = region_from_corners(Court(corners=tuple(map(tuple, corners))))
    elif get_preset(conn, preset_id) is None:
        raise LookupError(f"No such preset: {preset_id}")

    if derived_quad is not None:
        # Written directly, not through the user-preset API: the derived quad
        # has points outside 0-1 (the near sidelines leave the frame), which
        # PresetCreateBody's validator would refuse.
        preset_id = create_preset(conn, "Court from corners", derived_quad)
    # All validation passed. Write every column in one statement.
    _forget_scorer_if_corners_moved(conn, source, corners)
    set_source_setup(
        conn, source_id, rotation_deg, preset_id,
        mount=mount, play_format=play_format,
        court_corners=None if corners is None else [tuple(p) for p in corners],
    )
    return jobq.enqueue(conn, "build_proxy", {"source_id": source_id})


def apply_choices(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    mount: Any = KEEP,
    play_format: Any = KEEP,
    court_corners: Any = KEEP,
) -> None:
    """Change mount, format and corners on a source, enqueueing nothing.

    For a source that has already been through the pipeline: the proxy does
    not depend on these, so a rebuild would be fifteen wasted minutes. It
    never reassigns the play-region preset either -- a derived region would
    change court_preset_id and make the Detection panel price a re-detect for
    a fence source whose scorer reads corners, not the region.
    """
    source = get_source(conn, source_id)
    if source is None:
        raise LookupError(f"No such source: {source_id}")
    if source["status"] in BUSY_STATUSES:
        raise RuntimeError(f"source is {source['status']}; wait for that job to finish")
    mount, play_format, corners = validate_choices(source, mount, play_format, court_corners)
    _forget_scorer_if_corners_moved(conn, source, corners)
    set_source_choices(
        conn, source_id, mount=mount, play_format=play_format,
        court_corners=None if corners is None else [tuple(p) for p in corners],
    )
