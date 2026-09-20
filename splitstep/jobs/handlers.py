import logging
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from splitstep import resources
from splitstep.config import Library
from splitstep.db import jobs as jobq
from splitstep.db.rallies import list_rallies, replace_rallies, set_clip_path
from splitstep.db.reels import get_reel, mark_rendered
from splitstep.db.schema import connect, migrate
from splitstep.db.sessions import (
    add_source,
    find_ingesting_sources_by_original_name,
    find_or_create_session_for_date,
    find_sources_by_original_name,
    get_session,
    get_source,
    get_source_by_original_name,
    scoring_rules,
    set_session_status,
    set_source_dimensions,
    set_source_segment_threshold,
    set_source_status,
)
from splitstep.db.settings import get_color_profile, get_hr_clips_root, lock_color_profile
from splitstep.detect.audio import detect_hits, extract_pcm, hits_to_grid
from splitstep.detect.features import read_features, write_features
from splitstep.detect.geometry import Quad
from splitstep.detect.segment import params_for_frames, segment
from splitstep.detect.vision import build_features, iter_person_boxes
from splitstep.jobs.worker import Handler, no_progress
from splitstep.media.clips import clip_relpath
from splitstep.media.concat import concat_clips
from splitstep.media.files import find_original
from splitstep.media.numbered import make_numbered_intermediate, render_overlay_png
from splitstep.media.probe import display_size, probe
from splitstep.media.transcode import (
    HLG_PROFILE,
    ProgressFn,
    TranscodeError,
    make_clip,
    make_proxy,
    make_thumbs,
)
from splitstep.reels import (
    clip_paths,
    hr_clip_paths,
    hr_missing_count,
    missing_clip_count,
    resolve_items,
)
from splitstep.score import rules_from_dict, score_before, scoreboard_rows

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
    """The date a session gets filed under: the local evening it was played.

    `probe()` has already resolved `recorded_at` to local wall-clock time
    (see _recorded_at), so its date part is the answer directly. The mtime
    fallback -- for a clip whose metadata an editor or an `ffmpeg -c copy`
    remux stripped -- is read in local time for the same reason: a session
    played at 20:39 Eastern is a Tuesday session, and reading its timestamp
    as UTC would file it on Wednesday.
    """
    if recorded_at:
        return recorded_at[:10]
    mtime = datetime.fromtimestamp(fallback.stat().st_mtime, tz=UTC)
    return mtime.astimezone().date().isoformat()


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


def _session_should_fail(
    conn: sqlite3.Connection, session_id: str, failing_source_id: str | None
) -> bool:
    """Decide whether a source failure should also fail its session.

    True unless some OTHER source in the session already reached 'ready'.
    ('reviewed' is a SESSION-only status -- see the vocabulary table in the
    spec and refresh_session_review_status; sources never hold it, so
    checking for it here was always a no-op.) Sessions hold multiple
    sources (see test_second_file_same_day_joins_the_same_session), and
    refresh_session_review_status (splitstep/db/sessions.py) only ever acts
    on sessions already in ('ready', 'reviewed') -- once a session is
    written 'failed' it can never transition again. Failing it while a
    sibling source already finished review would strand that sibling's
    reviewed rallies behind this source's unrelated failure.
    """
    query = "SELECT 1 FROM sources WHERE session_id = ? AND status = 'ready'"
    params: list[str] = [session_id]
    if failing_source_id is not None:
        query += " AND id != ?"
        params.append(failing_source_id)
    row = conn.execute(query + " LIMIT 1", params).fetchone()
    return row is None


def handle_ingest(library: Library, payload: dict,
           _progress: ProgressFn = no_progress) -> None:
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
        # See _session_should_fail: don't strand a sibling source's
        # already-reviewed work behind this one's failure.
        if session_id is not None and _session_should_fail(conn, session_id, source_id):
            set_session_status(conn, session_id, "failed")
        _move_to_failed(library, src, f"{type(exc).__name__}: {exc}")
        raise


def handle_build_proxy(library: Library, payload: dict,
           _progress: ProgressFn = no_progress) -> None:
    """Transcode a registered source's proxy at its chosen rotation.

    Idempotent by overwrite: a proxy half-written by a killed worker is
    worthless, so a retry re-encodes rather than trying to resume.
    """
    conn = _open(library)
    source = get_source(conn, payload["source_id"])
    if source is None:
        raise ValueError(f"No such source: {payload['source_id']}")

    src_dir = library.source_dir(source["session_id"], source["idx"])
    original = find_original(src_dir)
    if original is None:
        raise ValueError(f"No original on disk for source {source['id']}")

    try:
        set_source_status(conn, source["id"], "building")
        # Proxy plus sprite sheet run roughly 1.5x the source in the worst case.
        library.require_free(int(original.stat().st_size * 1.5))
        proxy_path = src_dir / "proxy.mp4"
        make_proxy(original, proxy_path, rotation_deg=source["rotation_deg"])
        make_thumbs(proxy_path, src_dir / "thumbs.jpg")
        # Probe the proxy we just wrote rather than recomputing dimensions
        # from rotation_deg: it's the artifact everything downstream (the
        # player, `splitstep doctor`) actually reads, and add_source's
        # ingest-time seed goes stale the moment the wizard corrects
        # rotation after that seed was written (see set_source_dimensions).
        proxy_info = probe(proxy_path)
        set_source_dimensions(conn, source["id"], proxy_info.width, proxy_info.height)
    except Exception:
        set_source_status(conn, source["id"], "failed")
        # See _session_should_fail: don't strand a sibling source's
        # already-reviewed work behind this one's failure.
        if _session_should_fail(conn, source["session_id"], source["id"]):
            set_session_status(conn, source["session_id"], "failed")
        raise

    set_source_status(conn, source["id"], "ingested")
    set_session_status(conn, source["session_id"], "detecting")
    # reclaim_stale() can requeue build_proxy if a worker dies after this
    # enqueue but before the job itself is written 'done', re-running this
    # handler; a duplicate detect job would call replace_rallies again and
    # silently discard any rally boundaries a human hand-edited between the
    # two detect runs (replace_rallies only preserves starred/rejected).
    # enqueue_once holds BEGIN IMMEDIATE across check and insert, so two
    # concurrent serve processes racing this window cannot both enqueue.
    jobq.enqueue_once(conn, "detect", source["id"], {"source_id": source["id"]})


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
    the proxy only when it is gone (has_original=0), which nothing in the
    app causes -- the original is kept for the life of the library.
    """
    if source["has_original"]:
        original = find_original(src_dir)
        if original is not None:
            return original
    return proxy


def handle_detect(library: Library, payload: dict,
           _progress: ProgressFn = no_progress) -> None:
    conn = _open(library)
    source = get_source(conn, payload["source_id"])
    if source is None:
        raise ValueError(f"No such source: {payload['source_id']}")

    src_dir = library.source_dir(source["session_id"], source["idx"])
    proxy = src_dir / "proxy.mp4"
    features_path = src_dir / "features.jsonl"

    # The same try/except ingest and build_proxy carry, and for a sharper
    # reason here: this handler writes 'detecting' on the way in, and the
    # worker only ever touches the jobs table, so an escaping exception used
    # to leave the source pinned mid-flight with nothing that could ever
    # clear it -- the next detect is the only other writer of the status.
    # The interface then states the opposite of the truth: status.ts renders
    # 'detecting' as the active "Finding rallies" pill and emptyQueueCopy
    # promises "detection is still running", which is the exact lie its
    # docstring exists to remove. Three sources sat that way for fifteen
    # hours after a frozen build died on a missing matplotlib, with the jobs
    # badge the only surface that knew anything had failed.
    try:
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

        # Held rather than inlined: the resolved threshold is recorded below
        # so the re-segment slider can open on the number these rallies were
        # actually cut at. params_for_frames picks the profile per source, so
        # the value differs by source and a constant here would be wrong for
        # half of them (migration 015).
        params = params_for_frames(frames)
        intervals = segment(frames, params)
        replace_rallies(conn, source["session_id"], source["id"], intervals)
        # After replace_rallies, never before: the column describes the
        # rallies now in the table, and a write that landed ahead of a failed
        # rewrite would describe rallies that were never replaced.
        set_source_segment_threshold(conn, source["id"], params.threshold)
    except Exception:
        set_source_status(conn, source["id"], "failed")
        # See _session_should_fail: don't strand a sibling source's
        # already-reviewed work behind this one's failure.
        if _session_should_fail(conn, source["session_id"], source["id"]):
            set_session_status(conn, source["session_id"], "failed")
        raise

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


def _clip_color_profile(conn, library: Library, info, src: Path):
    """The colour profile this library's clips are locked to, resolving and
    locking it on first use.

    Priority: the stored setting; else, if clips already exist on disk, the
    legacy module-pinned HLG profile (every pre-migration clip was cut under
    it, so it is a fact about those files, not a guess); else this source's
    own tags, which become the library's profile permanently. Locked at
    check time rather than after a successful encode: within one `Worker`'s
    single thread nothing races it, and a first export that fails mid-encode
    for an unrelated reason still locked a profile read from valid tags --
    the owner's camera either way. That guarantee is per-process, not
    per-library: two `splitstep serve` instances pointed at the same
    `library.db` -- the exact "two workers on one library.db" scenario the
    Tauri single-instance plugin exists to prevent (see
    docs/superpowers/specs/2026-08-26-mac-app-distribution-design.md) -- each
    run their own single-threaded Worker, and two such processes racing each
    other's first clip export could both read `stored is None` and each lock
    their own reading. Last write wins, and concat's pre-flight parameter
    check (see `media/concat.py`) is the net under that: a mismatched profile
    shows up there as a divergence, not as a silently wrong reel.

    An untagged source can never become the profile: unknown pixels locking
    the library would bless every future untagged source, exactly the
    relabel-without-conversion _require_locked_color exists to refuse.
    """
    stored = get_color_profile(conn)
    if stored is not None:
        return stored
    # Both shapes, not just the legacy flat one: reconcile_clip_layout moves
    # a pre-existing library's clips into per-source folders at serve
    # startup, but this function must give the same answer whether it runs
    # before or after that sweep in a given process -- a library whose clips
    # have already been moved is exactly as much "clips already exist under
    # the legacy constants" as one that has not been swept yet, and a
    # single-shape glob would silently stop finding them the moment the
    # sweep runs.
    if any(library.sessions_dir.glob("*/clips/*.mp4")) or any(
        library.sessions_dir.glob("*/clips/*/*.mp4")
    ):
        lock_color_profile(conn, HLG_PROFILE)
        return HLG_PROFILE
    actual = (info.color_range, info.color_space, info.color_transfer,
              info.color_primaries)
    if None in actual:
        shown = tuple(field or "unset" for field in actual)
        raise TranscodeError(
            f"{src.name} is missing colour metadata (untagged: range={shown[0]} "
            f"space={shown[1]} transfer={shown[2]} primaries={shown[3]}), so it "
            f"cannot set this library's clip colour profile. Export a properly "
            f"tagged source first."
        )
    lock_color_profile(conn, actual)
    return actual


def handle_clip(library: Library, payload: dict,
                progress: ProgressFn = no_progress) -> None:
    """Cut one rally's span to a clip at the locked profile.

    Idempotent by overwrite, like every other handler: a clip half-written by
    a killed worker is worthless, so a retry re-encodes rather than resuming.
    """
    conn = _open(library)
    source = get_source(conn, payload["source_id"])
    if source is None:
        raise ValueError(f"No such source: {payload['source_id']}")

    src_dir = library.source_dir(source["session_id"], source["idx"])
    if source["has_original"]:
        src = find_original(src_dir)
        if src is None:
            raise ValueError(f"No original on disk for source {source['id']}")
    else:
        # No original on disk -- lost outside the app, since nothing here
        # deletes one. Cut from the proxy and let make_clip's scale/pad
        # conform it to the locked frame. The clip is upscaled from 1080p and
        # nothing anywhere records or shows that: the file is
        # indistinguishable from a 4K-sourced one except by eye. What it does
        # keep is concat-compatibility, which is the property that cannot be
        # compromised.
        #
        # This comment used to claim the UI flagged such a clip. It never did
        # -- `has_original` reaches the frontend as a field on the source type
        # and no component reads it -- and the claim was repeated in the spec
        # and in this handler's test. The honest home for that badge is the
        # reel builder's per-item row (spec 6.4), which does not exist yet.
        src = src_dir / "proxy.mp4"
        if not src.exists():
            raise ValueError(f"No proxy on disk for source {source['id']}")

    start_ms, end_ms = payload["start_ms"], payload["end_ms"]
    name = clip_relpath(source["idx"], start_ms, end_ms)
    dst = library.clips_dir(source["session_id"]) / name

    # A 4K CRF-20 clip runs roughly 4 MB per second of video. Doubling that
    # leaves room for the muxer's own scratch and refuses early rather than
    # dying at 90%, which is the whole point of the check.
    library.require_free(int((end_ms - start_ms) / 1000 * 4_000_000 * 2))

    # Probing here and again inside make_clip is two ffprobe calls (~50 ms
    # each) against minutes of encode -- cheaper than widening make_clip's
    # signature to take a pre-probed MediaInfo.
    profile = _clip_color_profile(conn, library, probe(src), src)

    # The one handler that reports progress, because it is the one whose
    # duration a human sits through: 4-8x realtime, so a 24-point session is
    # about half an hour. The others are longer still but run unattended
    # right after ingest, and nothing is waiting on a number for them.
    make_clip(src, dst, start_ms=start_ms, end_ms=end_ms,
              rotation_deg=source["rotation_deg"], on_progress=progress,
              color_profile=profile)

    # A reel item whose rally vanished under a re-segment carries no
    # rally_id (see plan_reel_export), and it must still be cuttable: the
    # cut needs a source and a span and nothing else. clip_path is a
    # convenience recorded on a rally when there is one -- the clip on disk
    # is the real artifact, and it is named for its span either way.
    rally_id = payload.get("rally_id")
    if rally_id is not None:
        set_clip_path(conn, rally_id, str(dst.relative_to(library.root)))


def handle_reel(library: Library, payload: dict,
                progress: ProgressFn = no_progress) -> None:
    """Concatenate a reel's clips into `reels/<slug>.mp4`.

    Idempotent by overwrite, like every other handler.

    Refuses while any clip is missing rather than cutting them itself. That
    is the same rule the render route enforces, repeated here rather than
    trusted: clips can be deleted between enqueue and run, and a reel is not
    a place to discover that four points are gone. Auto-enqueueing the cuts
    from inside a render would also turn one button into half an hour of
    encoding nobody asked for.
    """
    conn = _open(library)
    reel = get_reel(conn, payload["reel_id"])
    if reel is None:
        raise ValueError(f"No such reel: {payload['reel_id']}")

    items = resolve_items(library, conn, reel["id"])
    if not items:
        raise ValueError(f"Reel {reel['slug']} has no items to render")
    missing = missing_clip_count(items)
    if missing:
        raise ValueError(
            f"Reel {reel['slug']} has {missing} clip(s) not cut yet; "
            f"cut them before rendering"
        )

    inputs = clip_paths(library, items)
    if payload.get("hr"):
        # RallyMetrics's overlaid copies stand in for the clips, one for one.
        # Refused whole rather than substituted where present, the same rule
        # as missing clips: a reel where some points show heart rate and
        # some do not reads as a bug in the render, not as a choice.
        hr_root = get_hr_clips_root(conn)
        if hr_root is None:
            raise ValueError(
                "No RallyMetrics clips folder is configured; "
                "run `splitstep config set-hr-clips PATH`"
            )
        missing_hr = hr_missing_count(hr_root, items)
        if missing_hr:
            raise ValueError(
                f"Reel {reel['slug']} has {missing_hr} clip(s) with no heart-rate overlay yet; "
                f"render them in RallyMetrics first"
            )
        inputs = hr_clip_paths(hr_root, items)
    dst = library.reels_dir / f"{reel['slug']}.mp4"

    if payload.get("numbered"):
        # Space: every input re-encoded (~input size again) plus the concat
        # of the copies -- triple the plain render's bound, and the same
        # refuse-early rationale.
        library.require_free(sum(p.stat().st_size for p in inputs) * 3)
        # Clips exist (the missing check above), so the profile was locked
        # when they were exported -- or they predate migration 011, which is
        # exactly what HLG_PROFILE is the legacy answer for.
        profile = get_color_profile(conn) or HLG_PROFILE
        font = resources.overlay_font()
        tmp_dir = library.reels_dir / f".{reel['slug']}.numbered.{uuid.uuid4().hex[:8]}"
        tmp_dir.mkdir(parents=True)

        # The score entering each clip, replayed per session. Cached per
        # session because a reel is session-agnostic (items from several
        # matches can sit in one reel) and re-reading a session's rallies
        # per item would be N queries for one answer. An orphan item (no
        # rally) or an untracked session gets no board -- the counter and
        # note still burn as before.
        session_cache: dict[str, tuple[list, object] | None] = {}

        def board_for(item) -> list[list[str]] | None:
            if item.rally is None:
                return None
            if item.session_id not in session_cache:
                rules = scoring_rules(get_session(conn, item.session_id))
                session_cache[item.session_id] = (
                    None if rules is None
                    else ([dict(r) for r in list_rallies(conn, item.session_id)],
                          rules_from_dict(rules))
                )
            cached = session_cache[item.session_id]
            if cached is None:
                return None
            rallies, parsed = cached
            state, _unscored = score_before(rallies, item.rally["id"], parsed)
            return scoreboard_rows(state, parsed)

        try:
            intermediates: list[Path] = []
            total = len(items)
            for i, (item, src) in enumerate(zip(items, inputs), start=1):
                png_i = tmp_dir / f"{i:03d}.png"
                # Spaced like the review queue's own "10 / 122" pill -- the
                # burn mirrors the counter the reviewer already reads.
                render_overlay_png(
                    png_i, counter=f"{i} / {total}", note=item.note, font=font,
                    scoreboard=board_for(item),
                )
                dst_i = tmp_dir / f"{i:03d}.mp4"
                make_numbered_intermediate(
                    src, dst_i,
                    overlay_png=png_i,
                    color_profile=profile,
                )
                intermediates.append(dst_i)
                # One coarse tick per finished clip: the per-clip encode is
                # the unit a human waits through, and threading ffmpeg's own
                # progress through N sequential encodes would need offset
                # bookkeeping this job does not otherwise carry.
                progress(i / (total + 1))
            mode = concat_clips(intermediates, dst, on_progress=progress)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    else:
        # A -c copy remux is about the sum of its inputs. The re-encode
        # fallback can land either side of that, so double it -- and
        # refusing early beats dying at 90% of a twenty-minute reel, which is
        # the whole point of the check.
        library.require_free(sum(p.stat().st_size for p in inputs) * 2)

        # progress is threaded through to the re-encode fallback only -- the
        # copy is effectively instantaneous, and `activeJobsLabel` suppresses
        # the percentage entirely until a job reports one, so a copy simply
        # shows the job count. The fallback is minutes on a real reel and is
        # worth a bar.
        mode = concat_clips(inputs, dst, on_progress=progress)

    if mode == "reencode":
        # concat_clips already logged the mismatch that caused this; this
        # line is what ties it to a reel by name in the same log.
        log.warning("reel %s fell back to a re-encode", reel["slug"])

    # The membership `items` was resolved against, captured up front --
    # NOT re-queried here. A render can take minutes, and mark_rendered
    # compares this against the reel's membership AT THIS MOMENT to decide
    # whether dirty may be cleared; re-deriving it here would just re-read
    # the same possibly-changed-mid-render rows and always agree with
    # itself, defeating the check.
    rendered_membership = {(i.source_id, i.start_ms, i.end_ms) for i in items}
    mark_rendered(
        conn, reel["id"], str(dst.relative_to(library.root)), rendered_membership
    )


HANDLERS: dict[str, Handler] = {
    "ingest": handle_ingest,
    "build_proxy": handle_build_proxy,
    "detect": handle_detect,
    "clip": handle_clip,
    "reel": handle_reel,
}
