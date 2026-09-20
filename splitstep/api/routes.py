import os
import platform
import sqlite3
import subprocess
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from splitstep import appconfig
from splitstep.accel import detect_accel
from splitstep.db import jobs as jobq
from splitstep.db.labels import (
    FLAG_ORDER,
    VERDICTS,
    add_label,
    derive_boundary_flags,
    latest_label_for_span,
    latest_labels,
    parse_flags,
    record_boundary_correction,
    retract_label,
)
from splitstep.db.presets import create_preset, get_preset, list_presets
from splitstep.db.rallies import (
    NOTE_MAX_CHARS,
    create_rally,
    list_rallies,
    list_rallies_for_source,
    merge_into_previous,
    replace_rallies,
    set_bounds,
    set_note,
    set_point,
    set_rejected,
    set_seen,
    set_star,
    set_winner,
    split_rally,
)
from splitstep.db.reels import (
    ITEM_NOTE_MAX_CHARS,
    add_items,
    create_reel,
    delete_reel,
    find_reel_by_name,
    get_reel,
    get_reel_by_slug,
    list_reels,
    remove_item,
    rename_reel,
    set_item_note,
    set_order,
)
from splitstep.db.sessions import (
    get_session,
    get_source,
    list_sessions,
    list_sources,
    refresh_session_review_status,
    scoring_rules,
    set_scoring,
    set_source_preset,
)
from splitstep.db.settings import get_hr_clips_root, set_hr_clips_root
from splitstep.detect.features import read_features
from splitstep.detect.geometry import Quad
from splitstep.detect.segment import params_for_frames, sample_interval_ms, score_series, segment
from splitstep.export import SETS, column_for, plan_export, walk_clip_files
from splitstep.label_sample import DEFAULT_WINDOW_MS, sample_windows
from splitstep.media.clips import parse_clip_name
from splitstep.media.files import find_original
from splitstep.media.frames import extract_frame
from splitstep.media.probe import ProbeError
from splitstep.media.transcode import TranscodeError, rotation_filter
from splitstep.reels import (
    delete_rendered_file,
    hr_missing_count,
    missing_clip_count,
    plan_reel_export,
    rendered_file,
    resolve_items,
)
from splitstep.score import rules_from_dict, rules_to_dict
from splitstep.setup import queue_setup
from splitstep.watcher import VIDEO_SUFFIXES, is_ingestible_name

from .media import range_response

router = APIRouter()


class StarBody(BaseModel):
    starred: bool


class RejectBody(BaseModel):
    rejected: bool


class PointBody(BaseModel):
    point: bool


class WinnerBody(BaseModel):
    winner: Literal["", "a", "b"]


class ScoringBody(BaseModel):
    """The rules object, or null to stop tracking. Validation is
    splitstep.score.rules_from_dict's, surfaced as a 422 -- one validator
    for the CLI, the column and the route."""

    rules: dict | None

    @field_validator("rules")
    @classmethod
    def check_rules(cls, v: dict | None) -> dict | None:
        if v is None:
            return None
        try:
            return rules_to_dict(rules_from_dict(v))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class NoteBody(BaseModel):
    note: str

    @field_validator("note")
    @classmethod
    def check_note(cls, v: str) -> str:
        # Trim before measuring, and store what was measured: trailing
        # whitespace is invisible to the reviewer but would widen the rendered
        # caption pill, and a note that is 120 characters of text plus two
        # spaces is not over the limit in any sense the reviewer would accept.
        v = v.strip()
        if len(v) > NOTE_MAX_CHARS:
            raise ValueError(f"a note is at most {NOTE_MAX_CHARS} characters")
        return v


class BoundsBody(BaseModel):
    start_ms: int
    end_ms: int

    @model_validator(mode="after")
    def check_order(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


class SplitBody(BaseModel):
    at_ms: int


class CreateRallyBody(BaseModel):
    """A span the reviewer drew by hand, with no rally behind it yet.

    Deliberately carries no validator, unlike BoundsBody. Every rule about
    this span -- the MIN_RALLY_MS floor, both bounds inside the source's
    duration -- needs the source's duration_ms to check, which pydantic
    cannot see, and create_rally already enforces all of them. A partial
    copy here would answer 422 for the half it could check and 400 for the
    rest, so one incoherent span would arrive as two different statuses
    depending on which way it was incoherent.
    """

    start_ms: int
    end_ms: int


class SpanLabelBody(BaseModel):
    """A judgement about a span of a source, with no rally behind it.

    The sampled half of the corpus (see
    `docs/superpowers/plans/2026-09-18-unflagged-window-sampling.md`): a
    window the detector never proposed has no rally to address, so the span
    arrives in the body instead of being resolved server-side from immutable
    det_* columns. That is a real difference in trust -- these two integers
    are the client's word -- which is why they are validated here rather than
    left to `add_label`.
    """

    span_start_ms: int = Field(ge=0)
    span_end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def check_span(self):
        # A zero-length (or inverted) span judges no footage, and
        # `latest_labels` resolves per exact span -- such a row would sit in
        # the corpus forever matching nothing that could supersede it.
        if self.span_end_ms <= self.span_start_ms:
            raise ValueError("span_end_ms must be greater than span_start_ms")
        return self


class SpanVerdictBody(SpanLabelBody):
    verdict: str
    boundary_flags: list[str] = Field(default_factory=list)

    @field_validator("verdict")
    @classmethod
    def check_verdict(cls, v: str) -> str:
        if v not in VERDICTS:
            raise ValueError(f"verdict must be one of {list(VERDICTS)}")
        return v


class LabelBody(BaseModel):
    # `verdict` is required here even though the column is nullable. The only
    # writer of a verdict-less row is the bounds route (Task 3), which derives
    # everything server-side; an HTTP client asserting nothing at all would be
    # writing an empty judgement.
    verdict: str
    boundary_flags: list[str] = Field(default_factory=list)

    @field_validator("verdict")
    @classmethod
    def check_verdict(cls, v: str) -> str:
        if v not in VERDICTS:
            raise ValueError(f"verdict must be one of {list(VERDICTS)}")
        return v

    @field_validator("boundary_flags")
    @classmethod
    def check_flags(cls, v: list[str]) -> list[str]:
        unknown = set(v) - set(FLAG_ORDER)
        if unknown:
            raise ValueError(f"unknown boundary flag(s): {sorted(unknown)}")
        return v


class ResegmentBody(BaseModel):
    # ge/le are not just documentation: a NaN or +/-inf threshold (e.g. a
    # cleared numeric input in the UI serializing to "NaN") would otherwise
    # sail through as a valid float and blow up Starlette's JSON renderer
    # later. `nan >= 0.0` and `inf <= 1.0` are both False, so pydantic turns
    # every non-finite value into a clean 422 here instead.
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class PresetBody(BaseModel):
    preset_id: str


class PresetCreateBody(BaseModel):
    name: str
    points: list[list[float]]

    @field_validator("points")
    @classmethod
    def check_points(cls, v: list[list[float]]) -> list[list[float]]:
        if len(v) != 4:
            raise ValueError("a play region needs exactly 4 points")
        for point in v:
            if len(point) != 2:
                raise ValueError("each point must be an [x, y] pair")
            if not all(0.0 <= c <= 1.0 for c in point):
                raise ValueError("points are normalized and must be within 0-1")
        return v


class SetupBody(BaseModel):
    rotation_deg: int
    preset_id: str


class ExportBody(BaseModel):
    which: str

    @field_validator("which")
    @classmethod
    def check_which(cls, v: str) -> str:
        if v not in SETS:
            raise ValueError(f"which must be one of {list(SETS)}")
        return v


class _ReelNameBody(BaseModel):
    """`name` validation shared by create and rename, so the two can never
    drift apart: a name of pure whitespace must be refused identically in
    both places rather than reimplemented -- and forgotten -- in one of them.
    """

    name: str

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str) -> str:
        # Stripped here rather than at the call site: create derives the
        # slug from this same string, so pure whitespace must not slip
        # through and slug to the generic "reel" fallback. Rename has no
        # slug at stake, but must refuse the same input for the same
        # reason -- a blank-looking name nobody actually chose.
        name = v.strip()
        if not name:
            raise ValueError("a reel needs a name")
        return name


class ReelCreateBody(_ReelNameBody):
    pass


class ReelRenameBody(_ReelNameBody):
    pass


class SpanBody(BaseModel):
    """One reel item, addressed the way reel_items keys it.

    Never a rally_id: replace_rallies deletes every rally for a source on a
    sweep, so a client holding one has a reference that expires. A span does
    not -- it is what the clip on disk is named for.
    """

    source_id: str
    start_ms: int
    end_ms: int

    @model_validator(mode="after")
    def check_order(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self

    def as_tuple(self) -> tuple[str, int, int]:
        return (self.source_id, self.start_ms, self.end_ms)


class ReelItemsBody(BaseModel):
    items: list[SpanBody]


class ReelOrderBody(BaseModel):
    order: list[SpanBody]


class ItemNoteBody(BaseModel):
    source_id: str
    start_ms: int
    end_ms: int
    note: str

    @field_validator("note")
    @classmethod
    def check_note(cls, v: str) -> str:
        # Trim before measuring and store what was measured -- the same rule
        # NoteBody applies to rally notes, for the same reviewer-facing reason.
        v = v.strip()
        if len(v) > ITEM_NOTE_MAX_CHARS:
            raise ValueError(f"a note is at most {ITEM_NOTE_MAX_CHARS} characters")
        return v


class RenderBody(BaseModel):
    numbered: bool = False
    # Use RallyMetrics's heart-rate-overlaid copies of the clips. Refused
    # (409) while any item lacks one, the same shape as the cut check.
    hr: bool = False


class HrClipsBody(BaseModel):
    path: str | None


class SessionReelBody(BaseModel):
    which: str

    @field_validator("which")
    @classmethod
    def check_which(cls, v: str) -> str:
        if v not in SETS:
            raise ValueError(f"which must be one of {list(SETS)}")
        return v


class RevealBody(BaseModel):
    """A library-relative path -- a clip file, or a `clips/` directory --
    to reveal in Finder. No format validation here: the route's own
    resolve-then-contain check is what has to hold regardless of shape, so
    a validator that pre-approved "looks like a relpath" would just be a
    second, weaker copy of that same check.
    """

    relpath: str


def _conn(request: Request) -> sqlite3.Connection:
    return request.app.state.conns.get()


def _library(request: Request):
    return request.app.state.library



def _features_at(library, source: sqlite3.Row) -> str | None:
    """When this source's cached features were written, or None if it has
    never been detected.

    The mtime of features.jsonl rather than a column, because the file IS
    the record -- a column would have to be kept in step with a file the
    detect handler writes, and the two could then disagree about the one
    thing the client asks them: are these features older than the play
    region? Same UTC isoformat the database's own timestamps use, so the
    client can compare the two with a plain string comparison.
    """
    path = library.source_dir(source["session_id"], source["idx"]) / "features.jsonl"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        # Missing is the ordinary case (never detected); an unreadable
        # sessions/ directory lands here too, and "unknown" is the honest
        # answer for both -- the client's warning stays silent either way.
        return None
    return datetime.fromtimestamp(mtime, UTC).isoformat()


def _source_json(library, source: sqlite3.Row) -> dict:
    """A source row plus the one field that is not in it: `features_at`.

    Paired with the row's own `preset_assigned_at`, this is what lets the
    re-segment panel notice that a play region was assigned after the last
    detect -- re-segment only replays cached features, so it cannot see that
    region and would silently produce the same rallies again.
    """
    return {**dict(source), "features_at": _features_at(library, source)}


def _session_json(row) -> dict:
    d = dict(row)
    d["scoring"] = scoring_rules(row)
    return d


@router.get("/api/sessions")
def api_list_sessions(request: Request):
    conn = _conn(request)
    out = []
    for s in list_sessions(conn):
        counts = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(starred),0) AS starred,"
            " COALESCE(SUM(point),0) AS point"
            " FROM rallies WHERE session_id = ? AND rejected = 0",
            (s["id"],),
        ).fetchone()
        # Which source the library card takes its still from.
        # /media/{session_id}/{idx}/frame.jpg can already render one, but the
        # list carried no idx to build that URL with, and a client guessing
        # 1 would be wrong for any session whose first source was never
        # added. The lowest idx is the footage the session opens on.
        #
        # Deliberately not filtered by status: frame.jpg 404s cleanly when
        # there is no proxy yet, and the card falls back to a placeholder on
        # the image's own error. Encoding "which statuses have a proxy" here
        # too would be a second copy of the status vocabulary to keep in
        # step with jobs/handlers.py.
        thumb = conn.execute(
            "SELECT MIN(idx) AS idx FROM sources WHERE session_id = ?", (s["id"],)
        ).fetchone()
        out.append({
            **_session_json(s),
            "rally_count": counts["total"],
            "starred_count": counts["starred"],
            "point_count": counts["point"],
            "thumb_idx": thumb["idx"],
        })
    return out


@router.get("/api/sessions/{session_id}")
def api_get_session(session_id: str, request: Request):
    conn = _conn(request)
    session = get_session(conn, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    library = _library(request)
    return {
        "session": _session_json(session),
        "sources": [_source_json(library, r) for r in list_sources(conn, session_id)],
        "rallies": [dict(r) for r in list_rallies(conn, session_id)],
    }


@router.post("/api/sessions/{session_id}/scoring")
def api_set_scoring(session_id: str, body: ScoringBody, request: Request):
    conn = _conn(request)
    if get_session(conn, session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    set_scoring(conn, session_id, body.rules)
    return {"scoring": scoring_rules(get_session(conn, session_id))}


@router.post("/api/sessions/{session_id}/export")
def api_export(session_id: str, body: ExportBody, request: Request):
    conn = _conn(request)
    if get_session(conn, session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    library = _library(request)

    plan = plan_export(library, conn, session_id, body.which)
    for payload in plan.pending:
        jobq.enqueue(conn, "clip", payload)

    # `total` comes from the plan, not a second COUNT(*) -- two queries that
    # must agree is the shape that let already_cut silently absorb in-flight
    # and unavailable rallies before.
    return {
        "queued": len(plan.pending),
        "already_cut": plan.already_cut,
        "in_flight": plan.in_flight,
        "unavailable": plan.unavailable,
        "total": plan.total,
    }


@router.get("/api/sessions/{session_id}/clips")
def api_list_clips(session_id: str, request: Request):
    """Every cut clip on disk for this session, read straight off the
    filesystem rather than a database table.

    The disk is the truth for clips, the same reasoning `plan_export`'s
    existence checks and `find_orphan_clips` both rely on: a clip either
    exists at the path its span implies, or it does not, and nothing in the
    schema records which -- `rallies.clip_path` names what WAS cut, not what
    is on disk right now (see `delete_orphan_clips`'s docstring), so it is
    not a substitute for looking.

    Walk and gate mirror `find_orphan_clips` exactly (`walk_clip_files`,
    then `parse_clip_name` on each candidate's path relative to `clips_dir`)
    on purpose -- this route and the orphan sweep must agree on what counts
    as "a clip we cut", or the panel could list a file the sweep would
    delete out from under it, or hide one the sweep would leave alone.
    """
    conn = _conn(request)
    if get_session(conn, session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    library = _library(request)
    clips_dir = library.clips_dir(session_id)
    if not clips_dir.is_dir():
        # clips/ is created by the first encode -- see find_orphan_clips'
        # docstring. Absence is the ordinary pre-export state, not an error.
        return {"clips": []}

    clips = []
    for path in walk_clip_files(clips_dir):
        rel = str(path.relative_to(clips_dir))
        parsed = parse_clip_name(rel)
        if parsed is None:
            continue
        source_idx, start_ms, end_ms = parsed
        clips.append({
            "source_idx": source_idx, "start_ms": start_ms, "end_ms": end_ms,
            "relpath": rel, "size_bytes": path.stat().st_size,
        })
    clips.sort(key=lambda c: (c["source_idx"], c["start_ms"]))
    return {"clips": clips}


@router.post("/api/clips/reveal")
def api_reveal_clip(body: RevealBody, request: Request):
    """Ask Finder to reveal a clip file, or a `clips/` folder, on disk.

    A deliberate local-first affordance, not a general file browser: this
    only has to work on the same Mac the browser tab is already open on. The
    Tauri shell's Phase 3 (see the mac-app-distribution design doc under
    docs/superpowers/specs/) replaces this with a native reveal command;
    this stays a thin `open`/`open -R` shim until that lands.

    Containment before existence, and both before touching a subprocess:
    `body.relpath` is joined onto `library.root` and resolved, then checked
    with `is_relative_to` against the library root resolved the same way --
    `Path.__truediv__` silently discards the left operand when the right is
    absolute (`library.root / "/etc/passwd"` is just `Path("/etc/passwd")`,
    the same trap `rendered_file` in reels.py documents), which is exactly
    why the check has to run on the resolved result rather than trusting the
    join. A path that fails containment 404s the same as one that is simply
    missing -- the response never distinguishes "outside the library" from
    "not there", so this cannot be used to probe the filesystem outside the
    library root for what exists.
    """
    library = _library(request)
    resolved = (library.root / body.relpath).resolve()
    if not resolved.is_relative_to(library.root.resolve()) or not resolved.exists():
        raise HTTPException(status_code=404, detail="Not found")

    if platform.system() != "Darwin":
        return {"ok": False, "reason": "reveal is only supported on macOS"}

    # check=False, explicit: `open` exiting non-zero (Finder not running,
    # e.g. over SSH) is not this route's problem to raise on -- it already
    # did the one thing it is responsible for, asking Finder to reveal a
    # path that passed containment and existence.
    if resolved.is_dir():
        subprocess.run(["open", str(resolved)], check=False)
    else:
        subprocess.run(["open", "-R", str(resolved)], check=False)
    return {"ok": True}


def _session_id_for_rally(conn, rally_id: str) -> str:
    row = conn.execute(
        "SELECT session_id FROM rallies WHERE id = ?", (rally_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Rally not found")
    return row["session_id"]


def _rally_det_span(conn, rally_id: str):
    """The rally's immutable detector span, `None` if it has none, or 404.

    Every label anchors to det_start_ms/det_end_ms rather than the editable
    start_ms/end_ms, so this is resolved server-side and clients never send a
    span -- a client that computed it from stale rally data could otherwise
    anchor a judgement to a span the detector never produced.

    A hand-made half of a split rally has no detector span at all (see
    split_rally). Returning None rather than a row of NULLs forces every
    caller to decide what that means instead of writing a corpus row keyed
    on (NULL, NULL) -- a key nothing can ever resolve against, accumulating
    silently in an append-only table.
    """
    row = conn.execute(
        "SELECT source_id, det_start_ms, det_end_ms FROM rallies WHERE id = ?",
        (rally_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Rally not found")
    if row["det_start_ms"] is None:
        return None
    return row


@router.post("/api/rallies/{rally_id}/star")
def api_star(rally_id: str, body: StarBody, request: Request):
    conn = _conn(request)
    session_id = _session_id_for_rally(conn, rally_id)
    set_star(conn, rally_id, body.starred)
    return {"ok": True, "session_status": refresh_session_review_status(conn, session_id)}


@router.post("/api/rallies/{rally_id}/reject")
def api_reject(rally_id: str, body: RejectBody, request: Request):
    conn = _conn(request)
    session_id = _session_id_for_rally(conn, rally_id)
    set_rejected(conn, rally_id, body.rejected)
    return {"ok": True, "session_status": refresh_session_review_status(conn, session_id)}


@router.post("/api/rallies/{rally_id}/point")
def api_point(rally_id: str, body: PointBody, request: Request):
    conn = _conn(request)
    session_id = _session_id_for_rally(conn, rally_id)
    set_point(conn, rally_id, body.point)
    # Refreshes review status, unlike the label route: marking a point is a
    # ruling on the clip in the same family as star and reject, and
    # reviewed_at records that a human ruled on the rally at all.
    return {"ok": True, "session_status": refresh_session_review_status(conn, session_id)}


@router.post("/api/rallies/{rally_id}/winner")
def api_winner(rally_id: str, body: WinnerBody, request: Request):
    """Who won this point. A non-empty winner also marks the point (see
    set_winner).

    Deliberately does not check whether the session tracks a score. The
    winner column outlives the tracking switch -- set_scoring(None) leaves
    every rally's winner in place -- so writing one while tracking is off is
    consistent with the data model. And the client's undo re-syncs all four
    flags (star, reject, point, winner) on every undo: a 409 here would fail
    every undo in a session that never tracked a score, which cost more than
    the stale-tab guard it used to buy."""
    conn = _conn(request)
    session_id = _session_id_for_rally(conn, rally_id)
    set_winner(conn, rally_id, body.winner)
    return {"ok": True, "session_status": refresh_session_review_status(conn, session_id)}


@router.post("/api/rallies/{rally_id}/note")
def api_note(rally_id: str, body: NoteBody, request: Request):
    conn = _conn(request)
    # No refresh_session_review_status call, unlike star/reject/point: writing
    # a note is not a ruling on the rally (see set_note), so it must not move
    # the session's review status. Same reasoning the label route follows.
    set_note(conn, rally_id, body.note)
    return {"ok": True}


@router.post("/api/rallies/{rally_id}/seen")
def api_seen(rally_id: str, request: Request):
    """What persist.ts's skip case calls on a plain right-arrow: "a human
    looked at this", never "a human ruled on this". See set_seen.

    Refreshes session status like star/reject/point do, and unlike the note
    route -- because since migration 008 the status is computed from
    `seen_at`, which is exactly the column this route writes. Skipping the
    last unseen rally is an ordinary way to finish a pass, and the only way
    to finish one without ruling on anything, so omitting the refresh would
    leave a fully-skimmed session stuck on 'ready' with nothing left to
    click that could ever move it.
    """
    conn = _conn(request)
    session_id = _session_id_for_rally(conn, rally_id)
    set_seen(conn, rally_id)
    return {"ok": True, "session_status": refresh_session_review_status(conn, session_id)}


@router.post("/api/rallies/{rally_id}/bounds")
def api_bounds(rally_id: str, body: BoundsBody, request: Request):
    conn = _conn(request)
    # Resolved before the write, both to 404 on a rally a re-segment in
    # another tab already deleted (set_bounds alone would silently update
    # nothing and report success) and because det_* is what the label
    # anchors to.
    span = _rally_det_span(conn, rally_id)
    # Label before bounds, not after: set_bounds and record_boundary_correction
    # each commit independently, so whichever runs second is the one a crash
    # between the two can lose. Losing the bounds write just means the drag
    # didn't visibly save and the reviewer retries. Losing the label after
    # the bounds already landed is worse and silent -- the UI reports success
    # while the correction that was the whole point never reaches the corpus.
    # Ordering the label first turns that failure mode into a loud one: the
    # request fails and the reviewer retries, and a retried label is harmless
    # since it only reads immutable det_* (captured above) and appends a row
    # that `latest_labels` will supersede if needed.
    # Every drag is ground truth: det_start_ms sits immutable beside the
    # edited start_ms, so the difference is a signed detector error in
    # milliseconds. It used to be destroyed by the next replace_rallies;
    # recording it here is the cheaper half of the whole corpus, and costs
    # the reviewer no extra keystrokes.
    # A hand-made half has no detector span, so there is no detector error
    # for this drag to measure -- the whole point of the corpus write. The
    # bounds edit itself still lands; only the label is skipped.
    if span is not None:
        record_boundary_correction(
            conn,
            rally_id=rally_id,
            source_id=span["source_id"],
            det_start_ms=span["det_start_ms"],
            det_end_ms=span["det_end_ms"],
            true_start_ms=body.start_ms,
            true_end_ms=body.end_ms,
        )
    set_bounds(conn, rally_id, body.start_ms, body.end_ms)
    return {"ok": True}


@router.post("/api/rallies/{rally_id}/split")
def api_split(rally_id: str, body: SplitBody, request: Request):
    """Cut one rally in two. The second half carries no detector span.

    404 and 400 are separated deliberately: an unknown id is a stale client
    (a re-segment in another tab already deleted the rally), while a bad
    at_ms is a live client asking for something incoherent. The reviewer's
    recovery differs -- reload versus move the playhead -- so the two must
    not collapse into one status.
    """
    conn = _conn(request)
    row = conn.execute("SELECT id FROM rallies WHERE id = ?", (rally_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Rally not found")
    try:
        new_id = split_rally(conn, rally_id, body.at_ms)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "new_rally_id": new_id}


@router.post("/api/rallies/{rally_id}/merge")
def api_merge(rally_id: str, request: Request):
    """Absorb a hand-made half back into the rally that abuts it.

    Refused for a rally carrying a detector span -- see
    merge_into_previous. Same 404/400 split as api_split, for the same
    reason.
    """
    conn = _conn(request)
    row = conn.execute("SELECT id FROM rallies WHERE id = ?", (rally_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Rally not found")
    try:
        merge_into_previous(conn, rally_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/api/rallies/{rally_id}/label")
def api_label(rally_id: str, body: LabelBody, request: Request):
    conn = _conn(request)
    span = _rally_det_span(conn, rally_id)
    if span is None:
        raise HTTPException(
            status_code=400,
            detail="This rally has no detector span to judge — it was made by hand, "
                   "not proposed by the detector.",
        )
    # Mirror of the carry-forward in record_boundary_correction: this route
    # always writes true_start_ms/true_end_ms=NULL, so without copying a
    # boundary correction already on record for this exact span forward, the
    # append-only row this call writes -- being the newest -- would become
    # the one latest_labels returns, silently erasing the correction from
    # every reader (H1, docs/superpowers/specs/2026-08-21-rally-labelling-
    # design.md).
    prior = latest_label_for_span(conn, span["source_id"], span["det_start_ms"], span["det_end_ms"])
    true_start_ms = prior["true_start_ms"] if prior is not None else None
    true_end_ms = prior["true_end_ms"] if prior is not None else None

    # boundary_flags is NOT simply "carried forward" the same way true_* is,
    # nor simply "taken from the request" the way it looks at first glance
    # (and the way this route used to treat it, which was Finding 1). Once a
    # correction is carried forward, the flags implied by it are a pure
    # function of det_* vs true_* -- there is exactly one right answer, and
    # the client cannot be trusted to have supplied it: LabelController
    # (web/src/lib/labels.ts) skips every row whose verdict is NULL when it
    # seeds label-mode state, so a drag-only row's derived flags are never
    # shown to the reviewer, and boundary_flags=[] on this request is not a
    # considered choice to clear them -- it is every request's default,
    # informed by nothing. A non-empty-but-different client list is no more
    # trustworthy for the same reason: it is still a guess made blind to the
    # measurement now on record, so recomputing wins even then, discarding
    # whatever the client sent. Only when NO correction is being carried
    # forward is there no measurement to defer to, and the client's list is
    # the reviewer's own live, explicit judgement -- that case is honoured
    # as-is, unchanged from before this fix.
    if true_start_ms is not None and true_end_ms is not None:
        boundary_flags = derive_boundary_flags(
            span["det_start_ms"], span["det_end_ms"], true_start_ms, true_end_ms
        )
    else:
        boundary_flags = body.boundary_flags

    label_id = add_label(
        conn,
        source_id=span["source_id"],
        span_start_ms=span["det_start_ms"],
        span_end_ms=span["det_end_ms"],
        verdict=body.verdict,
        boundary_flags=boundary_flags,
        true_start_ms=true_start_ms,
        true_end_ms=true_end_ms,
        rally_id=rally_id,
    )
    # No session_status refresh: a label is a note about the detector, not a
    # review decision, and flipping a session to 'reviewed' because someone
    # labelled one clip would misreport the review pass.
    return {"ok": True, "id": label_id}


@router.post("/api/rallies/{rally_id}/label/retract")
def api_label_retract(rally_id: str, request: Request):
    """Withdraw the current verdict for this rally's detector span.

    Label mode's `U`. Deliberately its own route rather than
    `POST .../label` with a null verdict: a verdict-less label row is what a
    boundary drag writes, so overloading that body would make "the reviewer
    took their judgement back" and "the reviewer moved an edge" the same
    request, and the carry-forward rules for the two are opposites.

    Takes no body -- the span is resolved server-side from the immutable
    det_* columns, same as every other label write. Idempotent: retracting a
    span that carries no verdict writes nothing and still reports success,
    so a retry (or two tabs undoing the same clip) cannot pile up rows.
    """
    conn = _conn(request)
    span = _rally_det_span(conn, rally_id)
    # The "idempotent" claim above is about a span that exists and simply
    # carries no verdict -- retracting nothing then is a legitimate no-op.
    # A hand-made half has no span at all, which is a different state: there
    # was never a verdict this call could be undoing, so reporting success
    # would be reporting a retraction that could not have happened. Same
    # refusal api_label gives for the same reason -- it and this route are
    # the only two writers of a verdict onto rally_labels, and one of them
    # accepting what the other refuses is exactly the drift queue_setup
    # exists to rule out on the setup side.
    if span is None:
        raise HTTPException(
            status_code=400,
            detail="This rally has no detector span to retract a verdict for — it was "
                   "made by hand, not proposed by the detector.",
        )
    label_id = retract_label(
        conn,
        source_id=span["source_id"],
        span_start_ms=span["det_start_ms"],
        span_end_ms=span["det_end_ms"],
        rally_id=rally_id,
    )
    # No session_status refresh, same as api_label: a label is a note about
    # the detector, not a review decision.
    return {"ok": True, "id": label_id}


@router.post("/api/sources/{source_id}/rallies")
def api_create_rally(source_id: str, body: CreateRallyBody, request: Request):
    """Add one rally at a span the detector never proposed.

    The 404/400 split is api_split's, for the reason api_split gives: an
    unknown source is a stale client and the reviewer reloads, while a bad
    span is a live client asking for something incoherent and the reviewer
    moves the playhead. create_rally raises ValueError for both, so the
    source is resolved here first -- exactly as api_split resolves the rally
    first -- or every stale client would be told to move its playhead.

    No clip job is queued. Adding a span never starts an encode; cutting
    stays the explicit button, as it is everywhere else.
    """
    conn = _conn(request)
    if get_source(conn, source_id) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        rally_id = create_rally(conn, source_id, body.start_ms, body.end_ms)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "rally_id": rally_id}


@router.get("/api/sources/{source_id}/label-sample")
def api_label_sample(
    source_id: str,
    request: Request,
    n: int = Query(default=20, ge=1, le=200),
    seed: int = Query(default=0),
    window_ms: int = Query(default=DEFAULT_WINDOW_MS, ge=1000, le=60_000),
):
    """Windows for a blind labelling pass: half flagged, half ignored.

    Recomputed per request from the source's duration and its rallies'
    detector spans rather than stored. A seed is all the state there is, so
    a reviewer who reloads mid-sitting gets the same list back without a
    table, a migration, or anything to go stale when the source is
    re-segmented under them.

    The response says nothing about which windows the detector flagged. That
    omission is the feature -- see `label_sample.Window`.
    """
    conn = _conn(request)
    source = get_source(conn, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    intervals = [
        (r["det_start_ms"], r["det_end_ms"])
        for r in list_rallies_for_source(conn, source_id)
        if r["det_start_ms"] is not None
    ]
    windows = sample_windows(
        duration_ms=source["duration_ms"],
        intervals=intervals,
        n=n,
        seed=seed,
        window_ms=window_ms,
    )
    return {
        "seed": seed,
        "window_ms": window_ms,
        "windows": [{"start_ms": w.start_ms, "end_ms": w.end_ms} for w in windows],
    }


@router.post("/api/sources/{source_id}/label")
def api_span_label(source_id: str, body: SpanVerdictBody, request: Request):
    """Judge a span directly, with no rally in the way.

    The rally-addressed route (`/api/rallies/{id}/label`) is the one a review
    pass uses; this is the one a sampling pass uses. Both end in `add_label`
    against the same table with the same resolution rule, so a sampled
    judgement and a rally-anchored one are the same kind of row -- which is
    what lets `labels score` read them together.
    """
    conn = _conn(request)
    if get_source(conn, source_id) is None:
        raise HTTPException(status_code=404, detail="Source not found")

    # Same carry-forward the rally route performs, and for the same reason:
    # this route always writes true_*=NULL, so an existing boundary
    # correction on this exact span would be silently erased by the newer
    # row. A sampled window rarely coincides with a dragged rally's det span
    # -- but "rarely" is not "never", and the two writers agreeing by
    # construction is cheaper than reasoning about when they collide.
    prior = latest_label_for_span(conn, source_id, body.span_start_ms, body.span_end_ms)
    true_start_ms = prior["true_start_ms"] if prior is not None else None
    true_end_ms = prior["true_end_ms"] if prior is not None else None
    if true_start_ms is not None and true_end_ms is not None:
        boundary_flags = derive_boundary_flags(
            body.span_start_ms, body.span_end_ms, true_start_ms, true_end_ms
        )
    else:
        boundary_flags = body.boundary_flags

    label_id = add_label(
        conn,
        source_id=source_id,
        span_start_ms=body.span_start_ms,
        span_end_ms=body.span_end_ms,
        verdict=body.verdict,
        boundary_flags=boundary_flags,
        true_start_ms=true_start_ms,
        true_end_ms=true_end_ms,
        # No rally to name. rally_id is provenance only and carries no
        # foreign key, so NULL here is the honest value, not a gap.
        rally_id=None,
    )
    return {"ok": True, "id": label_id}


@router.post("/api/sources/{source_id}/label/retract")
def api_span_label_retract(source_id: str, body: SpanLabelBody, request: Request):
    """Withdraw the current verdict for a sampled span.

    Its own route rather than a null verdict on the one above, mirroring the
    rally pair exactly: a verdict-less row is what a boundary drag writes,
    and overloading one body with both meanings is what that split exists to
    prevent.
    """
    conn = _conn(request)
    if get_source(conn, source_id) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    label_id = retract_label(
        conn,
        source_id=source_id,
        span_start_ms=body.span_start_ms,
        span_end_ms=body.span_end_ms,
        rally_id=None,
    )
    return {"ok": True, "id": label_id}


@router.get("/api/sources/{source_id}/labels")
def api_source_labels(source_id: str, request: Request):
    conn = _conn(request)
    if get_source(conn, source_id) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    # source_id rides along so a caller merging labels from several sources
    # (LabelMode fetches one list per source and flattens them) can key on
    # (source_id, span) rather than span alone -- two independent sources'
    # timelines both start at 0 and segment() lands every edge on a fixed
    # sample grid, so identical (span_start_ms, span_end_ms) pairs across
    # sources of one session are entirely possible, and a span-only key would
    # let one source's verdict render on another source's rally (M2).
    return [
        {
            "source_id": r["source_id"],
            "span_start_ms": r["span_start_ms"],
            "span_end_ms": r["span_end_ms"],
            "verdict": r["verdict"],
            "boundary_flags": parse_flags(r["boundary_flags"]),
            "true_start_ms": r["true_start_ms"],
            "true_end_ms": r["true_end_ms"],
            "labelled_at": r["labelled_at"],
        }
        for r in latest_labels(conn, source_id)
    ]


@router.post("/api/sources/{source_id}/resegment")
def api_resegment(source_id: str, body: ResegmentBody, request: Request):
    conn = _conn(request)
    library = _library(request)
    source = get_source(conn, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    path = library.source_dir(source["session_id"], source["idx"]) / "features.jsonl"
    if not path.exists():
        raise HTTPException(status_code=409, detail="Source has not been detected yet")

    frames = read_features(path)
    intervals = segment(frames, params_for_frames(frames, threshold=body.threshold))
    count = replace_rallies(conn, source["session_id"], source_id, intervals)
    # replace_rallies inserts every new rally with reviewed_at NULL and
    # carries starred/rejected across by overlap, but never reviewed_at --
    # so a session that read "reviewed" before this call would otherwise
    # keep reading "reviewed" while none of its rallies has actually been
    # seen (worst case: a threshold raised too far yields zero rallies and
    # the session is stuck showing "reviewed" with nothing to review). The
    # guard inside refresh_session_review_status makes this a no-op for a
    # session still 'ingesting'/'detecting'/'failed'.
    return {
        "count": count,
        "session_status": refresh_session_review_status(conn, source["session_id"]),
    }


@router.post("/api/sources/{source_id}/detect")
def api_detect(source_id: str, request: Request):
    """Queue a full re-detect for one source.

    Always a full run, never reuse_features: this route exists for "I just
    assigned a play region", and the quad is applied when features are built,
    so cached features are already shaped by the old quad (see CLAUDE.md on
    --reuse-features). Idempotent at the queue: enqueue_once means mashing
    the button cannot stack duplicate fifteen-minute jobs.
    """
    conn = _conn(request)
    source = get_source(conn, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if source["status"] in ("needs_setup", "ingesting"):
        raise HTTPException(
            status_code=409,
            detail="This source has no proxy yet -- finish setup first.",
        )
    job_id = jobq.enqueue_once(conn, "detect", source_id, {"source_id": source_id})
    return {"job_id": job_id, "already_running": job_id is None}


@router.get("/api/sources/{source_id}/scores")
def api_scores(source_id: str, request: Request,
               threshold: float | None = Query(default=None, ge=0.0, le=1.0)):
    conn = _conn(request)
    library = _library(request)
    source = get_source(conn, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    path = library.source_dir(source["session_id"], source["idx"]) / "features.jsonl"
    if not path.exists():
        raise HTTPException(status_code=409, detail="Source has not been detected yet")

    frames = read_features(path)
    params = params_for_frames(frames, threshold=threshold)
    step_ms = sample_interval_ms(frames)
    return {
        "step_ms": step_ms,
        "threshold": params.threshold,
        "scores": [round(s, 4) for s in score_series(frames, params)],
    }


@router.post("/api/sources/{source_id}/preset")
def api_set_preset(source_id: str, body: PresetBody, request: Request):
    conn = _conn(request)
    if get_source(conn, source_id) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if get_preset(conn, body.preset_id) is None:
        raise HTTPException(status_code=404, detail="Preset not found")
    set_source_preset(conn, source_id, body.preset_id)
    return {"ok": True}


@router.get("/api/sources/{source_id}")
def api_get_source(source_id: str, request: Request):
    source = get_source(_conn(request), source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return dict(source)


@router.post("/api/sources/{source_id}/setup")
def api_setup(source_id: str, body: SetupBody, request: Request):
    """Apply a wizard decision: rotation, play region, then rebuild.

    Errors map by kind rather than by message: a bad angle is the caller's
    malformed input (400), a missing row is a 404, and a source with a job
    already running is a conflict the caller can retry (409).
    """
    try:
        job_id = queue_setup(_conn(request), source_id, body.rotation_deg, body.preset_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job_id}


@router.get("/api/library/stats")
def api_library_stats(request: Request):
    """Total bytes under the library root.

    Keep-everything is policy (Reclaim Space is rejected, see CLAUDE.md), so
    this number is the app's single disk affordance -- informational, with no
    action attached. A walk is file-count-bound, not byte-bound: a library is
    a few files per source, so a few hundred stats per call. Only the Library
    page calls it, once per visit, never from a poll.
    """
    library = _library(request)
    # Same hazard Library.open guards at startup: an unclean eject leaves an
    # empty mountpoint that still walks cleanly. Without this, a missing
    # drive renders as a confident "0 B" instead of an error.
    if not library.db_path.exists():
        raise HTTPException(
            status_code=503,
            detail="Library not reachable — is the drive plugged in?",
        )
    total = 0
    for root, _dirs, files in os.walk(library.root):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                continue  # a file deleted mid-walk is not an error
    return {"bytes": total}


class ModeBody(BaseModel):
    # Validated by pydantic rather than in the handler so an unknown mode is
    # a 422 like every other malformed body in this file, not a bespoke 400.
    mode: Literal["friend", "dev"]


@router.get("/api/config")
def api_config(request: Request):
    hr_root = get_hr_clips_root(_conn(request))
    return {
        "mode": appconfig.get_mode(),
        "hr_clips_root": str(hr_root) if hr_root else None,
        "hr_clips_available": bool(hr_root and hr_root.is_dir()),
    }


@router.post("/api/config/hr-clips")
def api_set_hr_clips(body: HrClipsBody, request: Request):
    """Point this library at RallyMetrics's clips folder, or clear it (null).
    Refuses a path that is not a directory right now: a typo must not be
    remembered as the place heart-rate reels come from."""
    conn = _conn(request)
    if body.path is None:
        set_hr_clips_root(conn, None)
    else:
        root = Path(body.path).expanduser()
        if not root.is_dir():
            raise HTTPException(status_code=400, detail=f"Not a directory: {root}")
        set_hr_clips_root(conn, root)
    return api_config(request)


@router.post("/api/config/mode")
def api_set_mode(body: ModeBody, request: Request):
    # The write path does NOT silently repair a corrupt config the way
    # get_mode's read path tolerates one: overwriting an unparseable file
    # could discard a hand-edited library path the user meant to keep. The
    # friendly sentence (fix or delete the file) travels as the detail.
    try:
        appconfig.set_mode(body.mode)
    except appconfig.LibraryUnconfigured as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"mode": appconfig.get_mode()}


@router.get("/api/jobs")
def api_jobs(request: Request):
    rows = _conn(request).execute(
        "SELECT id,type,status,progress,error,error_detail,created_at,finished_at"
        " FROM jobs ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    return [dict(r) for r in rows]


@router.post("/api/jobs/{job_id}/retry")
def api_retry_job(job_id: str, request: Request):
    conn = _conn(request)
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not jobq.retry(conn, job_id):
        # Present but not failed -- done, queued, or running again already.
        raise HTTPException(status_code=409, detail="Only a failed job can be retried")
    return {"ok": True}


@router.post("/api/import")
def api_import(request: Request, file: UploadFile):
    """Stream an upload into `_inbox/`, where the watcher takes over.

    The route writes, the watcher ingests -- importing this way and dropping
    a file in Finder are the same pipeline from the first probe onward.
    Loopback upload is fast enough for multi-GB originals, and it is the one
    mechanism that works identically in a plain browser and in the app.

    Written to a dot-prefixed temp name first: the watcher skips dotfiles,
    so it can never see a half-streamed upload (its is_stable check guards
    Finder copies, but an http stream that stalls for a while would pass a
    size-settle check while still incomplete). os.replace onto the final
    name is atomic within the filesystem, so the watcher sees either nothing
    or a complete file.
    """
    library = _library(request)
    name = Path(file.filename or "").name  # strip any client-supplied path
    # Strip leading dots before the suffix check. Left alone, ".hidden.mp4"
    # would land dot-prefixed in _inbox -- invisible to both the watcher
    # (which skips dotfiles on purpose, see above) and the inbox listing,
    # forever, while this route still returned 200. ".mp4" alone still has
    # no suffix once stripped ("mp4"), so it correctly falls through to 415.
    name = name.lstrip(".")
    if not name or not is_ingestible_name(name):
        raise HTTPException(
            status_code=415,
            detail="Not a video file. Supported: "
                   + ", ".join(sorted(VIDEO_SUFFIXES)),
        )
    dest = library.inbox / name
    if dest.exists():
        # Keep both: the same phone exports the same default names, and a
        # second session's IMG_0001 must not overwrite the first's.
        dest = library.inbox / f"{dest.stem}_{uuid.uuid4().hex[:8]}{dest.suffix}"
    tmp = library.inbox / f".upload-{uuid.uuid4().hex}{dest.suffix}"
    try:
        with tmp.open("wb") as out:
            while chunk := file.file.read(1024 * 1024):
                out.write(chunk)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return {"name": dest.name}


@router.get("/api/inbox")
def api_inbox(request: Request):
    """What the inbox is silently sitting on: files the watcher will never
    ingest (wrong suffix) and files that failed ingest (quarantined by
    _move_to_failed with a sibling .error.txt). Both were previously
    invisible to the UI -- "I dropped it and nothing happened" was the
    reported experience.
    """
    library = _library(request)
    unsupported = sorted(
        p.name for p in library.inbox.iterdir()
        if p.is_file() and not p.name.startswith(".")
        and not is_ingestible_name(p.name)
    )
    failed = []
    failed_dir = library.inbox / "failed"
    if failed_dir.is_dir():
        for err in sorted(failed_dir.glob("*.error.txt")):
            failed.append({
                "name": err.name.removesuffix(".error.txt"),
                "error": err.read_text().strip(),
            })
    return {"unsupported": unsupported, "failed": failed}


@router.get("/media/{session_id}/{idx}/proxy.mp4")
def api_proxy(session_id: str, idx: int, request: Request,
              range: str | None = Header(default=None)):
    path = _library(request).source_dir(session_id, idx) / "proxy.mp4"
    return range_response(path, range)


@router.get("/media/clips/{session_id}/{source_idx}/{name}")
def api_clip_media(session_id: str, source_idx: int, name: str, request: Request,
                    range: str | None = Header(default=None)):
    """Stream one cut clip with 206 range support, the same as `api_proxy`
    and `api_reel_media`.

    `rendered_file` in reels.py's docstring sets the repo's standard of
    care here: a path handed to a file operation must be checked against a
    *resolved* containment boundary, because a bare join can be defeated
    (`Path.__truediv__` silently discards the left operand when the right
    is absolute) and ".." components survive a naive string check. This
    route meets that standard by a different, stricter mechanism than
    `rendered_file`'s resolve-then-compare, rather than by copying it:
    `source_idx` is a typed `int` path param (Starlette's converter admits
    only digits, so no ".." or "/" can arrive through it), and `name` uses
    the default single-segment converter, whose regex is `[^/]+` -- a "/"
    cannot survive inside one path segment, so a multi-component escape
    like "../../etc/passwd" cannot even reach this function; Starlette's own
    routing has nowhere to send it. What is left after that -- a same-segment
    trick like a literal ".." as the whole `name` -- is exactly what the
    `parse_clip_name` gate below closes: its regex is anchored at both ends
    and admits only digits, a single interior dash, and a literal ".mp4"
    suffix, so ".." fails to parse and 404s before any path is built at all.
    Structural exclusion (there is no character the route or the regex will
    carry through to a filesystem call) rather than a check performed on a
    path already constructed, which is the stronger of the two shapes
    `rendered_file`'s reasoning distinguishes.
    """
    relpath = f"{source_idx:02d}/{name}"
    if parse_clip_name(relpath) is None:
        raise HTTPException(status_code=404, detail="Not found")
    path = _library(request).clips_dir(session_id) / f"{source_idx:02d}" / name
    return range_response(path, range)


@router.get("/api/court_presets")
def api_list_presets(request: Request):
    """Presentation only -- storage and ordering live in db/presets.py so the
    CLI (`splitstep preset list`) and this endpoint always agree on both.
    """
    rows = list_presets(_conn(request))
    return [
        {"id": r["id"], "name": r["name"],
         "points": [list(p) for p in Quad.from_json(r["quad"]).points],
         "created_at": r["created_at"]}
        for r in rows
    ]


@router.post("/api/court_presets")
def api_create_preset(body: PresetCreateBody, request: Request):
    """Validation belongs at the boundary: PresetCreateBody.check_points
    rejects a malformed body as a 422 before a Quad is ever constructed;
    db/presets.create_preset does the storage, shared with the CLI.
    """
    quad = Quad(tuple((x, y) for x, y in body.points))
    preset_id = create_preset(_conn(request), body.name, quad)
    return {"id": preset_id}


FRAME_CACHE_KEEP = 20  # frame.jpg (proxy scrubbing) -- unchanged, not the wizard's access pattern.
# preview.jpg's own cache key is `preview-{rot}-{at_ms}.jpg`, and the setup
# wizard's nine-tile grid requests all nine timestamps at each of the four
# candidate rotations as the user cycles through them (9 * 4 = 36 distinct
# files) -- comfortably fewer than FRAME_CACHE_KEEP's 20 would evict the
# tiles for a rotation the user just backed away from, forcing a re-decode
# of a 4K frame on the very next click back to it. Set well above 36 so a
# full cycle through all four rotations stays cache-resident at once.
PREVIEW_CACHE_KEEP = 48
# Two concurrent 4K HEVC decodes is what an 8 GB M2 Air absorbs without
# swapping. The setup wizard's nine-frame rotation/timestamp grid fires
# nine preview requests at once; the rest queue on this semaphore rather
# than all nine landing on the kernel's memory pressure handler together.
# This bounds concurrency; it does NOT make same-key requests safe against
# each other -- see the write-to-temp-then-rename in api_preview for what
# actually guarantees a reader is never served a torn file.
_PREVIEW_SLOTS = threading.Semaphore(2)


def _evict_old_frames(
    src_dir: Path, pattern: str = "frame-*.jpg", keep: int = FRAME_CACHE_KEEP
) -> None:
    """Frame extraction caches one file per requested at_ms (frame.jpg) or
    per requested rotation/at_ms (preview.jpg) with no eviction otherwise --
    a quad-editor session scrubbing through many timestamps, or a setup
    wizard trying every rotation, would grow the source directory without
    bound. Keep only the `keep` most recently touched files matching
    `pattern`, deleting the rest by mtime.

    Eviction is best-effort housekeeping, not correctness-critical for the
    request it runs inside: a concurrent eviction pass (another request
    racing this one) can unlink a file between this glob and this stat, or
    between this stat and this unlink. Default a vanished file's sort key to
    0 instead of letting `stat()` raise, and swallow any other OSError from
    the sweep, so a race here never turns an otherwise-successful frame
    request into a 500 -- the next eviction pass reconciles whatever this
    one didn't finish.
    """
    def _mtime_or_zero(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    try:
        files = sorted(src_dir.glob(pattern), key=_mtime_or_zero)
        for stale in files[:-keep]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass


def _last_safe_ms(source: sqlite3.Row) -> int:
    """Clamp a requested at_ms to a timestamp ffmpeg can actually decode.

    Scrubbing past a clip's end (or before its start) is ordinary UI
    behaviour in the quad editor. Past end-of-stream, ffmpeg fails with
    exit 234 and a misleading "Non full-range YUV is non-standard" message
    -- the same end-of-stream encoder bug make_thumbs already documents
    and clamps against (splitstep/media/transcode.py) -- so callers clamp
    into the clip's duration here too, rather than reject. This also makes
    the negative case explicit instead of relying on ffmpeg to silently
    clamp it (which produced a duplicate cached file per distinct negative
    value, all byte-identical to frame-0.jpg).

    Clamping to `duration_ms - 1` alone still reproduces the bug: at 30fps
    (2000ms/60 frames) the true last frame lands at ~1966.67ms, so 1999ms
    falls in the same post-last-frame gap the bug lives in (measured:
    1967ms fails, 1966ms succeeds; at a synthetic 10fps 1901ms fails,
    1900ms succeeds). The margin has to account for the source's own frame
    period, not just its reported duration.

    duration_ms/fps come from the sources row rather than a fresh probe():
    they are read once at ingest, from the original, and never re-timed --
    make_proxy passes no -r and only scales -- so they hold equally well
    for frame.jpg's proxy read and preview.jpg's original read.
    """
    fps, duration_ms = source["fps"], source["duration_ms"]
    margin_ms = int(1000 / fps) + 1 if fps > 0 else max(1, duration_ms // 2)
    return max(0, duration_ms - margin_ms)


@router.get("/media/{session_id}/{idx}/frame.jpg")
def api_frame(session_id: str, idx: int, request: Request, at_ms: int = 0):
    conn = _conn(request)
    library = _library(request)

    # Look the source up in the database, the way api_scores/api_resegment
    # already do, instead of shelling out to ffprobe on every request
    # (including cache hits): probe() has no timeout, so a wedged ffprobe
    # against e.g. a spun-down external drive would hold an anyio worker
    # thread forever out of Starlette's pool of 40, shared with every other
    # route. This also turns an unknown session_id/idx into a clean 404
    # instead of a filesystem probe.
    source = conn.execute(
        "SELECT * FROM sources WHERE session_id = ? AND idx = ?", (session_id, idx)
    ).fetchone()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    src_dir = library.source_dir(session_id, idx)
    proxy = src_dir / "proxy.mp4"
    if not proxy.is_file():
        raise HTTPException(status_code=404, detail="Proxy not found")

    at_ms = max(0, min(at_ms, _last_safe_ms(source)))

    dst = src_dir / f"frame-{at_ms}.jpg"
    # A crash-retry ingest reuses the same source_dir and overwrites
    # proxy.mp4 in place (see jobs/handlers.py's existing-source reuse for a
    # requeued job). Checking mtime, not just existence, keeps a frame
    # cached before that reuse from being served forever against footage it
    # no longer matches. `<=` (not `<`) so a same-tick mtime tie -- plausible
    # on coarse-granularity filesystems or fast successive writes -- still
    # regenerates instead of silently serving the stale file.
    if not dst.exists() or dst.stat().st_mtime <= proxy.stat().st_mtime:
        try:
            # Pinned rather than left to extract_frame's default: that
            # default is tuned for preview.jpg's 4K original reads (see
            # frames.py), and this route's own wedged-drive scenario above
            # needs the same 30s grace it always has, unaffected by that.
            extract_frame(proxy, dst, at_ms=at_ms, timeout=30.0)
        except (TranscodeError, ProbeError) as exc:
            # ProbeError and TranscodeError are unrelated exception classes,
            # but extract_frame's underlying ffmpeg call can surface either
            # against a proxy that is not yet a complete, valid video (e.g.
            # mid-transcode, or a crash-retry ingest that has not finished
            # rewriting proxy.mp4 yet). That is a normal transient state,
            # not a server error.
            raise HTTPException(
                status_code=409, detail="Source is still being processed"
            ) from exc
        _evict_old_frames(src_dir)
    else:
        # Cache hit: bump the mtime so a concurrent eviction sweep can't
        # unlink this exact file between this handler returning and
        # Starlette sending the body, and so eviction becomes
        # access-time-based rather than write-time-based -- a frame still
        # being scrubbed through stays hot instead of aging out.
        os.utime(dst, None)
    return FileResponse(dst, media_type="image/jpeg")


@router.get("/media/{session_id}/{idx}/preview.jpg")
def api_preview(
    session_id: str, idx: int, request: Request, at_ms: int = 0, rot: int = 0
):
    """Serve a rotated frame from the ORIGINAL, for the setup wizard.

    The wizard asks for rotation and a play region before build_proxy ever
    runs (see handle_ingest), so there is no proxy.mp4 to read a frame
    from yet -- only the original the source was ingested with. Caching
    keys on rotation as well as at_ms (`preview-{rot}-{at_ms}.jpg`) because
    the wizard's grid requests the same timestamp at every candidate
    rotation.
    """
    conn, library = _conn(request), _library(request)
    source = conn.execute(
        "SELECT * FROM sources WHERE session_id = ? AND idx = ?", (session_id, idx)
    ).fetchone()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        rotation_filter(rot)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    src_dir = library.source_dir(session_id, idx)
    original = find_original(src_dir)
    if original is None:
        raise HTTPException(status_code=404, detail="Original not available")

    at_ms = max(0, min(at_ms, _last_safe_ms(source)))
    dst = src_dir / f"preview-{rot}-{at_ms}.jpg"
    if not dst.exists():
        with _PREVIEW_SLOTS:
            # This re-check only spares an extraction for the third and
            # later request queued behind the same two slots: the first two
            # requests for this exact rot/at_ms can both already be past the
            # outer dst.exists() check above and both take a permit before
            # either has written dst, so both still reach extract_frame
            # below. What makes that safe is not this check but where each
            # writer extracts to -- see below.
            if not dst.exists():
                # Extract to a temp path unique to this call (not just this
                # rot/at_ms) and os.replace() it onto dst. os.replace is
                # atomic within a filesystem: a reader racing this either
                # sees no file yet, the old file, or the complete new one --
                # never bytes from an in-progress ffmpeg write. Two writers
                # for the same key can still both run ffmpeg (see above),
                # but they land on two distinct temp paths and only one
                # rename wins; neither can produce a torn dst. Named to
                # START with "preview-" (not a dotfile) so it still MATCHES
                # the "preview-*.jpg" glob _evict_old_frames sweeps below --
                # a process SIGKILLed between this line and the `finally`
                # unlink otherwise leaks the temp file forever, since a
                # dotfile name never matches that glob no matter how many
                # sweeps run. The embedded uuid keeps it unguessable and
                # guarantees it can never collide with a real `dst` name (no
                # route ever serves a path built from anything but rot/
                # at_ms), so a reader can still only ever be served the
                # complete `dst`, never this file, whether or not eviction
                # touches it first. The trailing .jpg is load-bearing --
                # ffmpeg's output muxer is inferred from the destination
                # filename's extension (run_ffmpeg passes no explicit -f),
                # so the temp path has to end in .jpg too or extraction
                # itself fails.
                tmp = src_dir / f"{dst.stem}.{uuid.uuid4().hex}{dst.suffix}"
                try:
                    extract_frame(
                        original, tmp, at_ms=at_ms, rotation_deg=rot,
                        hwaccel=detect_accel().hwaccel,
                    )
                    os.replace(tmp, dst)
                except (TranscodeError, ProbeError) as exc:
                    raise HTTPException(
                        status_code=409, detail="Source is still being processed"
                    ) from exc
                except FileNotFoundError as exc:
                    # tmp matching the eviction glob (see above) means a
                    # concurrent sweep could in principle unlink it between
                    # extract_frame finishing and this os.replace -- the
                    # same best-effort race _evict_old_frames' own docstring
                    # already accepts for completed cache files. Surface it
                    # the same way as a torn extraction rather than an
                    # unhandled 500: a caller retrying the request gets a
                    # fresh temp path and a fresh chance.
                    raise HTTPException(
                        status_code=409, detail="Source is still being processed"
                    ) from exc
                finally:
                    tmp.unlink(missing_ok=True)
        _evict_old_frames(src_dir, pattern="preview-*.jpg", keep=PREVIEW_CACHE_KEEP)
    else:
        os.utime(dst, None)
    return FileResponse(dst, media_type="image/jpeg")


def _reel_or_404(conn: sqlite3.Connection, slug: str) -> sqlite3.Row:
    reel = get_reel_by_slug(conn, slug)
    if reel is None:
        raise HTTPException(status_code=404, detail="Reel not found")
    return reel


def _item_json(item) -> dict:
    """One builder row. `rally` is None for an orphan -- an item whose span no
    rally holds any more, which the UI badges rather than hides."""
    return {
        "source_id": item.source_id,
        "session_id": item.session_id,
        "source_idx": item.source_idx,
        "start_ms": item.start_ms,
        "end_ms": item.end_ms,
        "duration_ms": item.duration_ms,
        "position": item.position,
        "clip_ready": item.clip_ready,
        "rally": item.rally,
        "note": item.note,
    }


@router.get("/api/reels")
def api_list_reels(request: Request):
    conn = _conn(request)
    # The cover still for each reel card: its first clip's own frame.
    # `reel_items` keys on (source_id, start_ms, end_ms) and not on rally_id,
    # so the session and idx that /media/.../frame.jpg needs come from the
    # source join rather than from a rally -- the same reason resolve_items
    # joins them, and the reason this works for an orphaned item too.
    #
    # One grouped query rather than a lookup per reel: this route renders
    # every reel on the list page, and it already runs on a shared worker
    # thread.
    covers = {
        r["reel_id"]: {"session_id": r["session_id"], "idx": r["idx"], "at_ms": r["start_ms"]}
        for r in conn.execute(
            "SELECT i.reel_id, i.start_ms, s.session_id, s.idx FROM reel_items i"
            " JOIN sources s ON s.id = i.source_id"
            " WHERE i.position = ("
            "   SELECT MIN(position) FROM reel_items WHERE reel_id = i.reel_id"
            " )"
        ).fetchall()
    }
    # None while a reel is empty, which is every reel between being created
    # and the builder adding to it.
    return [{**dict(r), "thumb": covers.get(r["id"])} for r in list_reels(conn)]


@router.post("/api/reels")
def api_create_reel(body: ReelCreateBody, request: Request):
    reel = create_reel(_conn(request), body.name)
    # item_count so a freshly created reel has the same shape as a listed
    # one; the list page renders straight from either.
    return {**dict(reel), "item_count": 0}


@router.get("/api/reels/{slug}")
def api_get_reel(slug: str, request: Request):
    conn = _conn(request)
    library = _library(request)
    reel = _reel_or_404(conn, slug)
    items = resolve_items(library, conn, reel["id"])
    # rendered_bytes on the DETAIL route only, never the list one: it needs
    # a stat() per reel, and the list page renders every reel on every page
    # load -- fine for one row here, a needless syscall storm there.
    rendered = rendered_file(library, reel)
    return {
        # item_count so a single reel has the SAME shape as a listed one.
        # The frontend shares one `Reel` type across both routes, so a
        # missing field here would be `undefined` at runtime while the type
        # promised a number -- silent until something rendered it. Taken
        # from the already-resolved items rather than a second COUNT(*), so
        # the two can never disagree.
        "reel": {
            **dict(reel),
            "item_count": len(items),
            "rendered_bytes": rendered.stat().st_size if rendered is not None else None,
        },
        "items": [_item_json(i) for i in items],
    }


@router.post("/api/reels/{slug}/rename")
def api_rename_reel(slug: str, body: ReelRenameBody, request: Request):
    conn = _conn(request)
    reel = _reel_or_404(conn, slug)
    rename_reel(conn, reel["id"], body.name)
    updated = get_reel(conn, reel["id"])
    # Same single-reel shape as api_create_reel: item_count from the
    # already-resolved items, no second COUNT(*).
    return {
        **dict(updated),
        "item_count": len(resolve_items(_library(request), conn, reel["id"])),
    }


@router.delete("/api/reels/{slug}")
def api_delete_reel(slug: str, request: Request):
    """Delete a reel and, if it has one, its rendered file -- file first.

    A crash between the two steps leaves either a stray file with no row
    pointing at it, or a stray row whose rendered_path points at a file
    that is already gone. Only one of those is recoverable through this
    app: a stray row is still visible in the reel list and Delete can
    simply be pressed again (delete_rendered_file already tolerates a
    rendered_path with nothing behind it, returning removed_file=False).
    A stray file has no row left to find it by, and nothing in this codebase
    scans `reels/` for orphans -- that is Reclaim Space, deferred to Plan 3
    (see CLAUDE.md). Deleting the row first would trade a recoverable leftover
    for an invisible one, silently defeating the whole point of this feature:
    the space it exists to give back.

    Refuses (409) while a render is queued or running for this reel. Without
    this, render -> render again -> delete reaches a state where the file
    this call unlinks is the OLD render, not the one `concat_clips` is still
    minutes into writing: the row is gone by the time that job's
    `os.replace()` lands the new file, `mark_rendered`'s UPDATE then matches
    zero rows (correctly -- it must not resurrect a deleted reel), and the
    file it just wrote sits in `reels/` with no row pointing at it and no
    scanner (Reclaim Space, Plan 3) able to find it. Checking first is the
    same idiom `api_render_reel` already uses for its own precondition (the
    "N clips not cut yet" 409); this is that idiom applied to a second one.
    """
    conn = _conn(request)
    reel = _reel_or_404(conn, slug)
    if jobq.pending_reel_job(conn, reel["id"]) is not None:
        raise HTTPException(
            status_code=409,
            detail="A render is in progress for this reel. Wait for it to finish, then delete.",
        )
    removed_file = delete_rendered_file(_library(request), reel)
    delete_reel(conn, reel["id"])
    return {"deleted": True, "removed_file": removed_file}


@router.post("/api/reels/{slug}/items")
def api_add_reel_items(slug: str, body: ReelItemsBody, request: Request):
    conn = _conn(request)
    reel = _reel_or_404(conn, slug)
    spans = [s.as_tuple() for s in body.items]
    added = add_items(conn, reel["id"], spans)
    # `existing` is reported separately rather than folded into a single
    # "total added" so a second click can honestly say "1 added, 2 already
    # there" instead of implying it did nothing. Deduped with set() because
    # these spans come straight from the client and may repeat -- the picker
    # can hand us the same rally twice (add_items already tolerates that; see
    # its docstring) -- and an undeduped count would double-report the same
    # already-there span as two.
    return {
        "added": added,
        "existing": len(set(spans)) - added,
        "total": len(resolve_items(_library(request), conn, reel["id"])),
    }


@router.post("/api/reels/{slug}/items/remove")
def api_remove_reel_item(slug: str, body: SpanBody, request: Request):
    conn = _conn(request)
    reel = _reel_or_404(conn, slug)
    removed = remove_item(conn, reel["id"], body.source_id, body.start_ms, body.end_ms)
    return {
        "removed": removed,
        "total": len(resolve_items(_library(request), conn, reel["id"])),
    }


@router.post("/api/reels/{slug}/order")
def api_set_reel_order(slug: str, body: ReelOrderBody, request: Request):
    conn = _conn(request)
    reel = _reel_or_404(conn, slug)
    try:
        set_order(conn, reel["id"], [s.as_tuple() for s in body.order])
    except ValueError as exc:
        # 409, not 422: the request is well-formed, the client's view of the
        # membership is simply stale (a removal in another tab, most likely).
        # Refetching is the fix, and the UI says so.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/api/reels/{slug}/items/note")
def api_set_item_note(slug: str, body: ItemNoteBody, request: Request):
    conn = _conn(request)
    reel = _reel_or_404(conn, slug)
    if not set_item_note(conn, reel["id"], body.source_id,
                         body.start_ms, body.end_ms, body.note):
        raise HTTPException(status_code=404, detail="No such item in this reel")
    return {"ok": True}


@router.post("/api/reels/{slug}/export")
def api_export_reel_clips(slug: str, request: Request):
    """Cut the clips this reel is missing. Never called by render."""
    conn = _conn(request)
    reel = _reel_or_404(conn, slug)
    plan = plan_reel_export(_library(request), conn, reel["id"])
    for payload in plan.pending:
        jobq.enqueue(conn, "clip", payload)
    return {
        "queued": len(plan.pending),
        "already_cut": plan.already_cut,
        "in_flight": plan.in_flight,
        "unavailable": plan.unavailable,
        "total": plan.total,
    }


@router.post("/api/reels/{slug}/render")
def api_render_reel(slug: str, request: Request, body: RenderBody | None = None):
    """Enqueue the concat. Refuses while any clip is missing, naming the count.

    Deliberately does NOT cut the missing clips: a button labelled "render"
    must not start half an hour of encoding. Cutting stays the separate,
    explicitly-pressed action next to it.

    `body` is optional so a plain render (no JSON body at all) keeps working
    -- the existing callers that never asked about numbering.
    """
    conn = _conn(request)
    reel = _reel_or_404(conn, slug)
    items = resolve_items(_library(request), conn, reel["id"])
    if not items:
        raise HTTPException(status_code=409, detail="This reel has no items yet.")
    missing = missing_clip_count(items)
    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"{missing} clip(s) not cut yet. Cut them first.",
        )

    # A double-click must not queue two concats onto one output path.
    # Returning the job already in flight makes the second press honest
    # rather than a silent no-op. enqueue_reel_once does the check-and-insert
    # under one write lock (see its docstring) -- this route must not inline
    # that SELECT itself, or it drifts back into the race the function exists
    # to close.
    hr = bool(body and body.hr)
    if hr:
        hr_root = get_hr_clips_root(conn)
        if hr_root is None:
            raise HTTPException(
                status_code=409,
                detail="No RallyMetrics clips folder is set. Run "
                       "`splitstep config set-hr-clips PATH` first.",
            )
        missing_hr = hr_missing_count(hr_root, items)
        if missing_hr:
            raise HTTPException(
                status_code=409,
                detail=f"{missing_hr} clip(s) have no heart-rate overlay yet. "
                       f"Render them in RallyMetrics first.",
            )
    job_id, already_running = jobq.enqueue_reel_once(
        conn, reel["id"], numbered=body.numbered if body else False, hr=hr
    )
    return {"job_id": job_id, "already_running": already_running}


@router.get("/media/reels/{slug}.mp4")
def api_reel_media(slug: str, request: Request, range: str | None = Header(default=None)):
    """Serve a rendered reel's mp4 with 206 range support, so <video> can seek.

    `slug` is used ONLY to look the reel up -- the path served comes from
    `rendered_path` in the database, never from a join against the URL. That
    makes traversal structurally impossible rather than filtered-against
    (there is no filesystem path built out of client input to sanitize), and
    it gives the right 404s for free: an unrendered reel has rendered_path
    IS NULL, so it 404s the same way an unknown slug does, with no special
    case needed here. A rendered_path whose file has since been deleted (or
    that names a directory) 404s the same way too, now that `rendered_file`
    itself requires `is_file()` -- this route's own `path is None` branch
    catches that case before `range_response` would ever get a chance to.

    The containment check itself -- resolve, then compare, because
    `Path.__truediv__` silently discards the left operand when the right is
    absolute (`library_root / "/etc/passwd"` is just `Path("/etc/passwd")`)
    -- lives in `rendered_file`, not here. `delete_rendered_file` needs the
    exact same question answered before it unlinks anything, and two copies
    of a security-relevant check is how they drift; see `rendered_file`'s
    docstring for the full reasoning.
    """
    reel = get_reel_by_slug(_conn(request), slug)
    if reel is None:
        raise HTTPException(status_code=404, detail="Reel not found")
    path = rendered_file(_library(request), reel)
    if path is None:
        raise HTTPException(status_code=404, detail="Reel not found")
    return range_response(path, range)


@router.post("/api/sessions/{session_id}/reels")
def api_session_reel(session_id: str, body: SessionReelBody, request: Request):
    """Create (or additively merge into) the reel for a session's point or
    starred set, and return its slug so the client can open the builder.

    Resolved by NAME, not by slug: the second click must land in the reel the
    first one made, and a hand-made reel that happens to slug the same is a
    different reel with a different name. When a name is new, unique_slug
    yields to whatever already holds the slug (see create_reel).

    Membership is added, never set. Overwriting would silently discard a
    manual reorder -- the same class of mistake replace_rallies makes with
    boundary edits, which already cost this project a 9.6-second rally.
    """
    conn = _conn(request)
    session = get_session(conn, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    column = column_for(body.which)
    rows = conn.execute(
        f"SELECT source_id, start_ms, end_ms FROM rallies WHERE session_id = ?"
        f" AND {column} = 1 AND rejected = 0 ORDER BY idx",
        (session_id,),
    ).fetchall()
    spans = [(r["source_id"], r["start_ms"], r["end_ms"]) for r in rows]

    name = f"{session['played_on']} {body.which}"
    reel = find_reel_by_name(conn, name) or create_reel(conn, name)
    added = add_items(conn, reel["id"], spans)

    # Deduped the same way as api_add_reel_items, though it is a no-op here:
    # `spans` comes from a `rallies` query keyed one row per rally, so the
    # (source_id, start_ms, end_ms) triple cannot repeat. Kept for
    # consistency rather than reasoning about two different formulas for the
    # same count.
    return {
        "slug": reel["slug"],
        "name": reel["name"],
        "added": added,
        "existing": len(set(spans)) - added,
        "total": len(resolve_items(_library(request), conn, reel["id"])),
    }
