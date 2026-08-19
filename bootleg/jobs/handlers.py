import logging
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from bootleg.config import Library
from bootleg.db import jobs as jobq
from bootleg.db.rallies import replace_rallies
from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import (
    add_source,
    find_or_create_session_for_date,
    get_source,
    set_session_status,
    set_source_status,
)
from bootleg.detect.audio import detect_hits, extract_pcm, hits_to_grid
from bootleg.detect.features import read_features, write_features
from bootleg.detect.geometry import Quad
from bootleg.detect.segment import SegmentParams, segment
from bootleg.detect.vision import build_features, iter_person_boxes
from bootleg.jobs.worker import Handler
from bootleg.media.probe import probe
from bootleg.media.transcode import make_proxy, make_thumbs

log = logging.getLogger(__name__)

SAMPLE_FPS = 5
STEP_MS = 1000 // SAMPLE_FPS
AUDIO_SR = 22050

# Must match make_thumbs' own default cadence -- this is the ceiling we
# scale down from for short clips, not an independent choice.
THUMB_INTERVAL_S = 10

# Whole frame. Replaced by a court preset once one exists for the source.
DEFAULT_QUAD = Quad(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))


def _open(library: Library) -> sqlite3.Connection:
    conn = connect(library.db_path)
    migrate(conn)
    return conn


def _played_on(recorded_at: str | None, fallback: Path) -> str:
    if recorded_at:
        return recorded_at[:10]
    ts = datetime.fromtimestamp(fallback.stat().st_mtime, tz=UTC)
    return ts.date().isoformat()


def _thumb_interval_s(duration_ms: int) -> int:
    """Seconds between sprite-sheet samples, scaled down for short clips.

    make_thumbs samples at a fixed cadence with `fps=1/every_s`. On this
    ffmpeg build (9.0.1), a clip much shorter than that interval forces the
    `fps` filter to emit its single frame from an end-of-stream flush, and
    that frame is tagged in a way the mjpeg encoder refuses ("Non
    full-range YUV is non-standard") -- reproduced directly against ffmpeg
    outside of bootleg, so it is not something make_thumbs' own args can
    route around case by case. Sampling well inside the stream rather than
    at its tail avoids the flush-frame path entirely. Long, real footage is
    unaffected: this only lowers the interval when the clip is too short
    for the default cadence to make sense anyway.
    """
    duration_s = max(1, duration_ms // 1000)
    return max(1, min(THUMB_INTERVAL_S, duration_s // 4))


def handle_ingest(library: Library, payload: dict) -> None:
    src = Path(payload["path"])
    conn = _open(library)

    info = probe(src)
    # Proxy plus sprite sheet run roughly 1.5x the source in the worst case.
    library.require_free(int(src.stat().st_size * 1.5))
    played_on = _played_on(info.recorded_at, src)
    session_id = find_or_create_session_for_date(conn, played_on)

    source_id, idx = add_source(
        conn, session_id,
        recorded_at=info.recorded_at or played_on,
        duration_ms=info.duration_ms,
        width=info.width, height=info.height, fps=info.fps,
        original_name=src.name,
    )

    dest_dir = library.source_dir(session_id, idx)
    dest_dir.mkdir(parents=True, exist_ok=True)
    original = dest_dir / f"original{src.suffix.lower()}"
    shutil.move(str(src), original)

    make_proxy(original, dest_dir / "proxy.mp4")
    make_thumbs(dest_dir / "proxy.mp4", dest_dir / "thumbs.jpg",
                every_s=_thumb_interval_s(info.duration_ms))

    set_source_status(conn, source_id, "ingested")
    set_session_status(conn, session_id, "detecting")
    jobq.enqueue(conn, "detect", {"source_id": source_id})


def _quad_for(conn: sqlite3.Connection, source: sqlite3.Row) -> Quad:
    if source["court_preset_id"]:
        row = conn.execute(
            "SELECT quad FROM court_presets WHERE id = ?",
            (source["court_preset_id"],),
        ).fetchone()
        if row:
            return Quad.from_json(row["quad"])
    return DEFAULT_QUAD


def _audio_source(src_dir: Path, proxy: Path, source: sqlite3.Row) -> Path:
    """Pick the file to pull audio from.

    The proxy's audio track is a 128k AAC re-encode of the original -- lossy
    re-encoding degrades exactly the high-frequency transients ball contacts
    produce, and audio impact detection is already marginal on windy public
    courts. Prefer the original while it still exists on disk; fall back to
    the proxy only once the original has been reclaimed (has_original=0).
    """
    if source["has_original"]:
        matches = sorted(src_dir.glob("original.*"))
        if matches:
            return matches[0]
    return proxy


def handle_detect(library: Library, payload: dict) -> None:
    conn = _open(library)
    source = get_source(conn, payload["source_id"])
    if source is None:
        raise ValueError(f"No such source: {payload['source_id']}")

    src_dir = library.source_dir(source["session_id"], source["idx"])
    proxy = src_dir / "proxy.mp4"
    features_path = src_dir / "features.jsonl"

    if payload.get("reuse_features") and features_path.exists():
        frames = read_features(features_path)
    else:
        set_source_status(conn, source["id"], "detecting")
        audio_path = _audio_source(src_dir, proxy, source)
        grid = _audio_grid(audio_path, source["duration_ms"])
        quad = _quad_for(conn, source)
        boxes = list(iter_person_boxes(proxy, sample_fps=SAMPLE_FPS))
        frames = build_features(boxes, quad, grid, STEP_MS)
        write_features(features_path, frames)

    intervals = segment(frames, SegmentParams())
    replace_rallies(conn, source["session_id"], source["id"], intervals)

    set_source_status(conn, source["id"], "ready")
    if all(r["status"] == "ready" for r in conn.execute(
            "SELECT status FROM sources WHERE session_id = ?",
            (source["session_id"],))):
        set_session_status(conn, source["session_id"], "ready")


def _audio_grid(path: Path, duration_ms: int) -> list[tuple[int, float]]:
    try:
        pcm = extract_pcm(path, sr=AUDIO_SR)
    except Exception:  # noqa: BLE001 -- files with no usable audio must not fail detect
        log.warning("no usable audio in %s; continuing without it", path)
        return []
    return hits_to_grid(detect_hits(pcm, AUDIO_SR), duration_ms, step_ms=STEP_MS)


HANDLERS: dict[str, Handler] = {
    "ingest": handle_ingest,
    "detect": handle_detect,
}
