import logging
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from bootleg.config import Library
from bootleg.db import jobs as jobq
from bootleg.db.rallies import replace_rallies
from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import (
    add_source,
    find_ingesting_sources_by_original_name,
    find_or_create_session_for_date,
    find_sources_by_original_name,
    get_source,
    get_source_by_original_name,
    set_session_status,
    set_source_status,
)
from bootleg.detect.audio import detect_hits, extract_pcm, hits_to_grid
from bootleg.detect.features import read_features, write_features
from bootleg.detect.geometry import Quad
from bootleg.detect.segment import SegmentParams, segment
from bootleg.detect.vision import build_features, iter_person_boxes
from bootleg.jobs.worker import Handler
from bootleg.media.probe import display_size, probe
from bootleg.media.transcode import make_proxy, make_thumbs

log = logging.getLogger(__name__)

SAMPLE_FPS = 5
STEP_MS = 1000 // SAMPLE_FPS
AUDIO_SR = 22050

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


def _move_to_failed(library: Library, src: Path, message: str) -> None:
    """Quarantine a file that failed ingest so the watcher stops re-queuing
    it every scan. A silent skip is invisible; a file sitting in
    `_inbox/failed/` next to the error that killed it is the first thing a
    human finds.
    """
    if not src.exists():
        # Already moved into place (failure happened after the final move,
        # e.g. a trivial DB error) or already quarantined by an earlier
        # attempt -- nothing left in the inbox to move.
        return
    failed_dir = library.inbox / "failed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    dest = failed_dir / src.name
    if dest.exists():
        dest = failed_dir / f"{src.stem}.{uuid.uuid4().hex[:8]}{src.suffix}"
    shutil.move(str(src), dest)
    (failed_dir / f"{dest.name}.error.txt").write_text(message)


def handle_ingest(library: Library, payload: dict) -> None:
    """Register a dropped file. No transcode, no detection.

    Both wait for a human to confirm orientation and play region in the
    setup wizard, which enqueues `build_proxy`. Registering is seconds of
    probing and a move, so a file dropped in the inbox shows up in the UI
    immediately instead of after a ten-minute round trip that may have been
    encoding it sideways the whole time.
    """
    src = Path(payload["path"])
    conn = _open(library)
    session_id: str | None = None
    source_id: str | None = None

    try:
        if not src.exists():
            # A crashed job is identified by status, never by name or row
            # order: original_name alone doesn't scope to one session (a
            # phone can reuse IMG_0001.MOV across days), so picking a row
            # out of an unscoped, name-only match is always an arbitrary
            # tiebreak. Exactly one 'ingesting' row for this name is the
            # crashed job -- finish it if its move landed, else raise; more
            # than one is ambiguous and must raise rather than guess. Zero
            # 'ingesting' rows means nothing is stranded: whether this is a
            # redundant requeue of a finished job or a payload that never
            # existed is answered without picking a row either, by asking
            # whether ANY same-named source has a completed original on disk.
            ingesting = find_ingesting_sources_by_original_name(conn, src.name)
            if len(ingesting) > 1:
                ids = ", ".join(sorted(c["id"] for c in ingesting))
                raise FileNotFoundError(
                    f"ingest payload names a missing inbox file matching "
                    f"multiple 'ingesting' sources ({ids}); refusing to "
                    f"guess which one to finish: {src}"
                )
            if len(ingesting) == 1:
                crashed = ingesting[0]
                moved_dir = library.source_dir(crashed["session_id"], crashed["idx"])
                if any(moved_dir.glob("original.*")):
                    set_source_status(conn, crashed["id"], "needs_setup")
                    set_session_status(conn, crashed["session_id"], "needs_setup")
                    return
            elif any(
                any(library.source_dir(s["session_id"], s["idx"]).glob("original.*"))
                for s in find_sources_by_original_name(conn, src.name)
            ):
                return
            raise FileNotFoundError(
                f"ingest payload names a missing inbox file with no completed "
                f"source to recover it from: {src}"
            )
        info = probe(src)
        # The transcode's space is checked in build_proxy, where it happens.
        # A move needs only what the file already occupies.
        library.require_free(src.stat().st_size)
        played_on = _played_on(info.recorded_at, src)
        session_id = find_or_create_session_for_date(conn, played_on)

        existing = get_source_by_original_name(conn, session_id, src.name)
        if existing is not None:
            source_id, idx = existing["id"], existing["idx"]
        else:
            width, height = display_size(info.width, info.height, info.rotation_deg)
            source_id, idx = add_source(
                conn, session_id,
                recorded_at=info.recorded_at or played_on,
                duration_ms=info.duration_ms,
                width=width, height=height, fps=info.fps,
                original_name=src.name,
                rotation_deg=info.rotation_deg,
            )

        dest_dir = library.source_dir(session_id, idx)
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), dest_dir / f"original{src.suffix.lower()}")

        set_source_status(conn, source_id, "needs_setup")
        set_session_status(conn, session_id, "needs_setup")
    except Exception as exc:
        if source_id is not None:
            set_source_status(conn, source_id, "failed")
        if session_id is not None:
            set_session_status(conn, session_id, "failed")
        _move_to_failed(library, src, f"{type(exc).__name__}: {exc}")
        raise


def handle_build_proxy(library: Library, payload: dict) -> None:
    """Transcode a registered source's proxy at its chosen rotation.

    Idempotent by overwrite: a proxy half-written by a killed worker is
    worthless, so a retry re-encodes rather than trying to resume.
    """
    conn = _open(library)
    source = get_source(conn, payload["source_id"])
    if source is None:
        raise ValueError(f"No such source: {payload['source_id']}")

    src_dir = library.source_dir(source["session_id"], source["idx"])
    original = _original_path(src_dir)
    if original is None:
        raise ValueError(f"No original on disk for source {source['id']}")

    try:
        set_source_status(conn, source["id"], "building")
        # Proxy plus sprite sheet run roughly 1.5x the source in the worst case.
        library.require_free(int(original.stat().st_size * 1.5))
        make_proxy(original, src_dir / "proxy.mp4", rotation_deg=source["rotation_deg"])
        make_thumbs(src_dir / "proxy.mp4", src_dir / "thumbs.jpg")
    except Exception:
        set_source_status(conn, source["id"], "failed")
        set_session_status(conn, source["session_id"], "failed")
        raise

    set_source_status(conn, source["id"], "ingested")
    set_session_status(conn, source["session_id"], "detecting")
    jobq.enqueue(conn, "detect", {"source_id": source["id"]})


def _original_path(src_dir: Path) -> Path | None:
    matches = sorted(src_dir.glob("original.*"))
    return matches[0] if matches else None


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
        original = _original_path(src_dir)
        if original is not None:
            return original
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
    "build_proxy": handle_build_proxy,
    "detect": handle_detect,
}
