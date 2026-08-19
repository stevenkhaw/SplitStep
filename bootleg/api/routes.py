import sqlite3

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, model_validator

from bootleg.db.presets import get_preset
from bootleg.db.rallies import list_rallies, replace_rallies, set_bounds, set_rejected, set_star
from bootleg.db.sessions import (
    get_session,
    get_source,
    list_sessions,
    list_sources,
    set_source_preset,
)
from bootleg.detect.features import read_features
from bootleg.detect.segment import SegmentParams, segment

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


@router.post("/api/rallies/{rally_id}/star")
def api_star(rally_id: str, body: StarBody, request: Request):
    set_star(_conn(request), rally_id, body.starred)
    return {"ok": True}


@router.post("/api/rallies/{rally_id}/reject")
def api_reject(rally_id: str, body: RejectBody, request: Request):
    set_rejected(_conn(request), rally_id, body.rejected)
    return {"ok": True}


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
