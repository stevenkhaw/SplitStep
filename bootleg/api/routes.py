import sqlite3
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator, model_validator

from bootleg.db.presets import get_preset
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
    threshold: float = SegmentParams().threshold


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
    return {"count": count}


@router.get("/api/sources/{source_id}/scores")
def api_scores(source_id: str, request: Request,
               threshold: float = SegmentParams().threshold):
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
    rows = _conn(request).execute(
        "SELECT id, name, quad, created_at FROM court_presets ORDER BY created_at DESC"
    ).fetchall()
    return [
        {"id": r["id"], "name": r["name"],
         "points": [list(p) for p in Quad.from_json(r["quad"]).points],
         "created_at": r["created_at"]}
        for r in rows
    ]


@router.post("/api/court_presets")
def api_create_preset(body: PresetCreateBody, request: Request):
    quad = Quad(tuple((x, y) for x, y in body.points))
    preset_id = uuid.uuid4().hex
    _conn(request).execute(
        "INSERT INTO court_presets (id, name, quad, created_at) VALUES (?,?,?,?)",
        (preset_id, body.name, quad.to_json(), datetime.now(UTC).isoformat()),
    )
    _conn(request).commit()
    return {"id": preset_id}


@router.get("/media/{session_id}/{idx}/frame.jpg")
def api_frame(session_id: str, idx: int, request: Request, at_ms: int = 0):
    library = _library(request)
    src_dir = library.source_dir(session_id, idx)
    proxy = src_dir / "proxy.mp4"
    if not proxy.is_file():
        raise HTTPException(status_code=404, detail="Proxy not found")

    dst = src_dir / f"frame-{at_ms}.jpg"
    if not dst.exists():
        extract_frame(proxy, dst, at_ms=at_ms)
    return FileResponse(dst, media_type="image/jpeg")
