import os
import sqlite3
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from bootleg.accel import detect_accel
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
from bootleg.detect.segment import params_for_frames, sample_interval_ms, score_series, segment
from bootleg.media.files import find_original
from bootleg.media.frames import extract_frame
from bootleg.media.probe import ProbeError
from bootleg.media.transcode import TranscodeError, rotation_filter
from bootleg.setup import queue_setup

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
    and clamps against (bootleg/media/transcode.py) -- so callers clamp
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
