import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from bootleg.db.presets import create_preset, get_preset, list_presets
from bootleg.db.rallies import (
    list_rallies,
    mark_reviewed,
    replace_rallies,
    set_bounds,
    set_rejected,
    set_star,
)
from bootleg.db.sessions import (
    get_session,
    get_source,
    list_sessions,
    list_sources,
    refresh_session_review_status,
    set_source_preset,
)
from bootleg.detect.features import read_features
from bootleg.detect.geometry import Quad
from bootleg.detect.segment import SegmentParams, sample_interval_ms, score_series, segment
from bootleg.media.frames import extract_frame
from bootleg.media.probe import ProbeError
from bootleg.media.transcode import TranscodeError

from .media import range_response

router = APIRouter()


class StarBody(BaseModel):
    starred: bool


class RejectBody(BaseModel):
    rejected: bool


class BoundsBody(BaseModel):
    start_ms: int
    end_ms: int

    @model_validator(mode="after")
    def check_order(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


class ResegmentBody(BaseModel):
    # ge/le are not just documentation: a NaN or +/-inf threshold (e.g. a
    # cleared numeric input in the UI serializing to "NaN") would otherwise
    # sail through as a valid float and blow up Starlette's JSON renderer
    # later. `nan >= 0.0` and `inf <= 1.0` are both False, so pydantic turns
    # every non-finite value into a clean 422 here instead.
    threshold: float = Field(default=SegmentParams().threshold, ge=0.0, le=1.0)


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


def _conn(request: Request) -> sqlite3.Connection:
    return request.app.state.conns.get()


def _library(request: Request):
    return request.app.state.library


@router.get("/api/sessions")
def api_list_sessions(request: Request):
    conn = _conn(request)
    out = []
    for s in list_sessions(conn):
        counts = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(starred),0) AS starred"
            " FROM rallies WHERE session_id = ? AND rejected = 0",
            (s["id"],),
        ).fetchone()
        out.append({
            **dict(s),
            "rally_count": counts["total"],
            "starred_count": counts["starred"],
        })
    return out


@router.get("/api/sessions/{session_id}")
def api_get_session(session_id: str, request: Request):
    conn = _conn(request)
    session = get_session(conn, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session": dict(session),
        "sources": [dict(r) for r in list_sources(conn, session_id)],
        "rallies": [dict(r) for r in list_rallies(conn, session_id)],
    }


def _session_id_for_rally(conn, rally_id: str) -> str:
    row = conn.execute(
        "SELECT session_id FROM rallies WHERE id = ?", (rally_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Rally not found")
    return row["session_id"]


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


@router.post("/api/rallies/{rally_id}/reviewed")
def api_reviewed(rally_id: str, request: Request):
    conn = _conn(request)
    session_id = _session_id_for_rally(conn, rally_id)
    mark_reviewed(conn, rally_id)
    return {"ok": True, "session_status": refresh_session_review_status(conn, session_id)}


@router.post("/api/rallies/{rally_id}/bounds")
def api_bounds(rally_id: str, body: BoundsBody, request: Request):
    set_bounds(_conn(request), rally_id, body.start_ms, body.end_ms)
    return {"ok": True}


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

    intervals = segment(read_features(path), SegmentParams(threshold=body.threshold))
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


@router.get("/api/sources/{source_id}/scores")
def api_scores(source_id: str, request: Request,
               threshold: float = Query(default=SegmentParams().threshold, ge=0.0, le=1.0)):
    conn = _conn(request)
    library = _library(request)
    source = get_source(conn, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    path = library.source_dir(source["session_id"], source["idx"]) / "features.jsonl"
    if not path.exists():
        raise HTTPException(status_code=409, detail="Source has not been detected yet")

    frames = read_features(path)
    params = SegmentParams(threshold=threshold)
    step_ms = sample_interval_ms(frames)
    return {
        "step_ms": step_ms,
        "threshold": threshold,
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


@router.get("/api/jobs")
def api_jobs(request: Request):
    rows = _conn(request).execute(
        "SELECT id,type,status,progress,error,created_at,finished_at"
        " FROM jobs ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/media/{session_id}/{idx}/proxy.mp4")
def api_proxy(session_id: str, idx: int, request: Request,
              range: str | None = Header(default=None)):
    path = _library(request).source_dir(session_id, idx) / "proxy.mp4"
    return range_response(path, range)


@router.get("/api/court_presets")
def api_list_presets(request: Request):
    """Presentation only -- storage and ordering live in db/presets.py so the
    CLI (`bootleg preset list`) and this endpoint always agree on both.
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


FRAME_CACHE_KEEP = 20


def _evict_old_frames(src_dir: Path, keep: int = FRAME_CACHE_KEEP) -> None:
    """Frame extraction caches one file per requested at_ms with no
    eviction otherwise -- a quad-editor session scrubbing through many
    timestamps would grow this directory without bound. Keep only the
    `keep` most recently touched frame-*.jpg, deleting the rest by mtime.

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
        frames = sorted(src_dir.glob("frame-*.jpg"), key=_mtime_or_zero)
        for stale in frames[:-keep]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass


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

    # Scrubbing past a clip's end (or before its start) is ordinary UI
    # behaviour in the quad editor. Past end-of-stream, ffmpeg fails with
    # exit 234 and a misleading "Non full-range YUV is non-standard"
    # message -- the same end-of-stream encoder bug make_thumbs already
    # documents and clamps against (bootleg/media/transcode.py) -- so clamp
    # into the clip's duration here too, rather than reject. This also
    # makes the negative case explicit instead of relying on ffmpeg to
    # silently clamp it (which produced a duplicate cached file per
    # distinct negative value, all byte-identical to frame-0.jpg).
    #
    # Clamping to `duration_ms - 1` alone still reproduces the bug: at
    # 30fps (2000ms/60 frames) the true last frame lands at ~1966.67ms, so
    # 1999ms falls in the same post-last-frame gap the bug lives in
    # (measured: 1967ms fails, 1966ms succeeds; at a synthetic 10fps
    # 1901ms fails, 1900ms succeeds). The margin has to account for the
    # source's own frame period, not just its reported duration.
    #
    # duration_ms/fps come from the sources row rather than probe(proxy):
    # make_proxy passes no -r and only scales, so the proxy's duration and
    # frame rate match the recorded values already in the database.
    fps = source["fps"]
    duration_ms = source["duration_ms"]
    margin_ms = int(1000 / fps) + 1 if fps > 0 else max(1, duration_ms // 2)
    last_safe_ms = max(0, duration_ms - margin_ms)
    at_ms = max(0, min(at_ms, last_safe_ms))

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
            extract_frame(proxy, dst, at_ms=at_ms)
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
