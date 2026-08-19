import argparse
import json
import logging
import sys
from pathlib import Path

from bootleg.config import Library, LibraryAlreadyInitialized, LibraryNotMounted
from bootleg.db import jobs as jobq
from bootleg.db.presets import create_preset, get_preset, list_presets
from bootleg.db.rallies import list_rallies, replace_rallies
from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import get_source, set_source_preset
from bootleg.detect.features import read_features
from bootleg.detect.geometry import Quad
from bootleg.detect.segment import SegmentParams, segment
from bootleg.jobs.handlers import HANDLERS
from bootleg.jobs.worker import Worker
from bootleg.watcher import InboxWatcher


def _library(args) -> Library:
    return Library.open(Path(args.library).expanduser())


def _format_ts(ms: int) -> str:
    """Format milliseconds as M:SS.s, or H:MM:SS.s once past one hour.

    Integer deciseconds throughout, not float division -- a real session
    runs past the one-hour mark and rounding a float seconds value to one
    decimal place can round 59.95 up to "60.0" instead of carrying into the
    next minute. Working in integer deciseconds and letting // and % do the
    carry means 59.95s prints as "1:00.0", never "0:60.0".
    """
    total_ds = round(ms / 100)
    ds = total_ds % 10
    total_s = total_ds // 10
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    if h:
        return f"{h}:{m:02d}:{s:02d}.{ds}"
    return f"{m}:{s:02d}.{ds}"


def _parse_quad(raw: str) -> Quad:
    """Parse '--quad "x1,y1 x2,y2 x3,y3 x4,y4"' into a validated Quad.

    Raises ValueError with a message naming the offending point on any
    malformed input -- wrong point count, a missing comma, or a non-numeric
    coordinate. Quad.__post_init__ does the rest of the validation (exactly
    4 points, each coercible to a float pair).
    """
    points = []
    for chunk in raw.split():
        parts = chunk.split(",")
        if len(parts) != 2:
            raise ValueError(f"malformed quad point {chunk!r}: expected 'x,y'")
        try:
            points.append((float(parts[0]), float(parts[1])))
        except ValueError as exc:
            raise ValueError(f"malformed quad point {chunk!r}: {exc}") from exc
    return Quad(tuple(points))


def cmd_init(args) -> int:
    # Deliberately not _library(args): that requires library.db to already
    # exist, which is exactly what this command creates.
    lib = Library.create(Path(args.library).expanduser())
    print(f"initialized library at {lib.root}")
    for sub in (lib.inbox, lib.sessions_dir, lib.reels_dir):
        print(f"  {sub}")
    print(f"  {lib.db_path}")
    return 0


def cmd_doctor(args) -> int:
    from bootleg.accel import detect_accel

    accel = detect_accel()
    lib = _library(args)
    print(f"library:      {lib.root}")
    print(f"hwaccel:      {accel.hwaccel or 'none (software)'}")
    print(f"h264 encoder: {accel.h264_encoder}")
    print(f"torch device: {accel.torch_device}")
    for sub in (lib.inbox, lib.sessions_dir, lib.reels_dir):
        print(f"{'ok ' if sub.is_dir() else 'MISSING'} {sub}")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from bootleg.api.app import create_app

    lib = _library(args)
    app = create_app(lib, spa_dist=Path(__file__).parent.parent / "web" / "dist")

    worker = Worker(lib, HANDLERS)
    watcher = InboxWatcher(lib)
    worker.start()
    watcher.start()

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        watcher.stop()
        worker.stop()
    return 0


def cmd_ingest(args) -> int:
    lib = _library(args)
    conn = connect(lib.db_path)
    migrate(conn)
    job_id = jobq.enqueue(conn, "ingest", {"path": str(Path(args.path).resolve())})
    print(f"queued ingest {job_id}")
    if args.now:
        Worker(lib, HANDLERS).run_once()
        print("done")
    return 0


def cmd_detect(args) -> int:
    lib = _library(args)
    conn = connect(lib.db_path)
    migrate(conn)
    payload = {"source_id": args.source_id, "reuse_features": args.reuse_features}
    jobq.enqueue(conn, "detect", payload)
    if args.now:
        Worker(lib, HANDLERS).run_once()
    return 0


def cmd_segment(args) -> int:
    """Re-run segmentation on cached features and print the result.

    The tuning loop: change a threshold, see the rally count, no YOLO.
    """
    lib = _library(args)
    conn = connect(lib.db_path)
    migrate(conn)
    source = get_source(conn, args.source_id)
    if source is None:
        print(f"no such source: {args.source_id}", file=sys.stderr)
        return 1

    path = lib.source_dir(source["session_id"], source["idx"]) / "features.jsonl"
    frames = read_features(path)
    params = SegmentParams(threshold=args.threshold)
    intervals = segment(frames, params)

    if args.dry_run:
        for i, iv in enumerate(intervals, 1):
            print(f"{i:3d}  {_format_ts(iv.start_ms):>9} → {_format_ts(iv.end_ms):>9}"
                  f"  ({(iv.end_ms-iv.start_ms)/1000:5.1f}s)  conf {iv.confidence:.2f}")
        print(f"\n{len(intervals)} rallies at threshold {args.threshold}")
        return 0

    replace_rallies(conn, source["session_id"], args.source_id, intervals)
    print(f"wrote {len(intervals)} rallies")
    print(json.dumps([dict(r) for r in list_rallies(conn, source["session_id"])],
                     indent=2)[:2000])
    return 0


def cmd_preset_add(args) -> int:
    """Create a court preset from four normalized 0-1 points.

    Spec Stage 0.5: the user drags four corners over frame 1, extended to
    the bottom of frame so the near player's feet stay inside the region.
    This one manual step is what makes adjacent courts disappear from
    detection -- without a preset, `_quad_for` always falls back to
    DEFAULT_QUAD (the whole frame).
    """
    lib = _library(args)
    conn = connect(lib.db_path)
    migrate(conn)
    try:
        quad = _parse_quad(args.quad)
    except ValueError as exc:
        print(f"error: invalid --quad: {exc}", file=sys.stderr)
        return 2
    preset_id = create_preset(conn, args.name, quad)
    print(preset_id)
    return 0


def cmd_preset_list(args) -> int:
    lib = _library(args)
    conn = connect(lib.db_path)
    migrate(conn)
    for row in list_presets(conn):
        points = " ".join(f"{x:.4f},{y:.4f}" for x, y in Quad.from_json(row["quad"]).points)
        print(f"{row['id']}  {row['name']}  {points}")
    return 0


def cmd_source_set_preset(args) -> int:
    lib = _library(args)
    conn = connect(lib.db_path)
    migrate(conn)
    if get_source(conn, args.source_id) is None:
        print(f"no such source: {args.source_id}", file=sys.stderr)
        return 1
    if get_preset(conn, args.preset_id) is None:
        print(f"no such preset: {args.preset_id}", file=sys.stderr)
        return 1
    set_source_preset(conn, args.source_id, args.preset_id)
    print(f"source {args.source_id} now uses preset {args.preset_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(prog="bootleg")
    parser.add_argument("--library", required=True, help="path to the library root")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create a new library tree and database")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("doctor", help="show detected hardware and library state")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("serve", help="run the web server, worker and inbox watcher")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8420)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("ingest", help="queue a video file for ingest")
    p.add_argument("path")
    p.add_argument("--now", action="store_true", help="run the job immediately")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("detect", help="queue detection for a source")
    p.add_argument("source_id")
    p.add_argument("--reuse-features", action="store_true")
    p.add_argument("--now", action="store_true")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("segment", help="re-segment cached features")
    p.add_argument("source_id")
    p.add_argument("--threshold", type=float, default=SegmentParams().threshold)
    p.add_argument("--dry-run", action="store_true",
                   help="print intervals without writing rallies")
    p.set_defaults(func=cmd_segment)

    p = sub.add_parser("preset", help="manage court presets")
    preset_sub = p.add_subparsers(dest="preset_command", required=True)

    pp = preset_sub.add_parser("add", help="create a court preset from four points")
    pp.add_argument("--name", required=True)
    pp.add_argument("--quad", required=True,
                    help="four normalized 0-1 points: 'x1,y1 x2,y2 x3,y3 x4,y4'")
    pp.set_defaults(func=cmd_preset_add)

    pl = preset_sub.add_parser("list", help="list court presets")
    pl.set_defaults(func=cmd_preset_list)

    p = sub.add_parser("source", help="manage sources")
    source_sub = p.add_subparsers(dest="source_command", required=True)

    sp = source_sub.add_parser("set-preset", help="assign a court preset to a source")
    sp.add_argument("source_id")
    sp.add_argument("preset_id")
    sp.set_defaults(func=cmd_source_set_preset)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except LibraryNotMounted as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except LibraryAlreadyInitialized as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
