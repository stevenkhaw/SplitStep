import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from splitstep import appconfig, resources
from splitstep.config import Library, LibraryAlreadyInitialized, LibraryNotMounted
from splitstep.db import jobs as jobq
from splitstep.db.labels import latest_labels, parse_flags
from splitstep.db.presets import create_preset, get_preset, list_presets
from splitstep.db.rallies import list_rallies, list_rallies_for_source, replace_rallies
from splitstep.db.schema import connect, migrate
from splitstep.db.sessions import (
    get_session,
    get_source,
    refresh_session_review_status,
    scoring_rules,
    set_scoring,
    set_source_preset,
)
from splitstep.detect.features import read_features
from splitstep.detect.geometry import Quad
from splitstep.detect.segment import params_for_frames, segment
from splitstep.export import (
    SETS,
    delete_orphan_clips,
    find_orphan_clips,
    plan_export,
    reconcile_clip_layout,
)
from splitstep.jobs.handlers import HANDLERS
from splitstep.jobs.worker import Worker
from splitstep.label_sample import DEFAULT_WINDOW_MS, sample_windows
from splitstep.label_score import rows_to_labels, score_against_labels
from splitstep.score import DEFAULT_RULES, rules_from_dict, score_before, scoreboard_rows
from splitstep.setup import queue_setup
from splitstep.watcher import InboxWatcher


def _library(args) -> Library:
    return Library.open(appconfig.resolve_library(args.library))


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
    lib = Library.create(appconfig.resolve_library(args.library))
    print(f"initialized library at {lib.root}")
    for sub in (lib.inbox, lib.sessions_dir, lib.reels_dir):
        print(f"  {sub}")
    print(f"  {lib.db_path}")
    return 0


def cmd_doctor(args) -> int:
    from splitstep.accel import detect_accel

    accel = detect_accel()
    lib = _library(args)
    print(f"library:      {lib.root}")
    print(f"hwaccel:      {accel.hwaccel or 'none (software)'}")
    print(f"h264 encoder: {accel.h264_encoder}")
    print(f"torch device: {accel.torch_device}")
    for sub in (lib.inbox, lib.sessions_dir, lib.reels_dir):
        print(f"{'ok ' if sub.is_dir() else 'MISSING'} {sub}")
    conn = connect(lib.db_path)
    migrate(conn)
    rows = conn.execute(
        "SELECT session_id, idx, status, width, height, rotation_deg FROM sources"
        " ORDER BY session_id, idx"
    ).fetchall()
    if rows:
        print("sources:")
        for r in rows:
            print(f"  {r['session_id']}/{r['idx']:02d}  {r['status']:<12}"
                  f"  {r['width']}x{r['height']}  rotation {r['rotation_deg']}")
    return 0


def cmd_config_show(args) -> int:
    cfg = appconfig.load_config()
    print(f"config file: {appconfig.config_path()}")
    print(f"library:     {cfg.get('library') or 'unset'}")
    env = os.environ.get(appconfig.ENV_VAR)
    if env:
        print(f"{appconfig.ENV_VAR} (overrides the file): {env}")
    return 0


def cmd_config_set_hr_clips(args) -> int:
    """A library setting, not app config: the overlaid clips pair with this
    library's clips, and a second library would have its own folder."""
    from splitstep.db.settings import set_hr_clips_root

    lib = _library(args)
    conn = connect(lib.db_path)
    migrate(conn)
    try:
        if args.path.lower() == "none":
            set_hr_clips_root(conn, None)
            print("heart-rate clips folder cleared")
            return 0
        path = Path(args.path).expanduser()
        if not path.is_dir():
            print(f"not a directory: {path}", file=sys.stderr)
            return 1
        set_hr_clips_root(conn, path)
        print(f"heart-rate clips folder: {path}")
        return 0
    finally:
        conn.close()


def cmd_config_set_library(args) -> int:
    path = Path(args.path).expanduser()
    if not path.is_dir():
        # Refuse rather than mkdir: choosing where a library lives is the
        # picker's/user's job, and a typo here must not create a stray tree.
        print(f"not a directory: {path}", file=sys.stderr)
        return 1
    cfg = appconfig.load_config()
    cfg["library"] = str(path)
    appconfig.save_config(cfg)
    # The app's library chooser reads this same list, so a library reached
    # from the terminal has to join it -- otherwise `libraries` quietly means
    # "opened from the app" while claiming to mean "opened".
    appconfig.remember_library(path)
    print(f"library set to {path}")
    return 0


def cmd_setup(args) -> int:
    lib = _library(args)
    conn = connect(lib.db_path)
    migrate(conn)
    preset_id = args.preset
    if preset_id is None:
        # Fetch the source to check both existence and assigned preset.
        # Distinguish missing source from source-exists-but-no-preset so we
        # report the right problem to the user.
        row = conn.execute(
            "SELECT court_preset_id FROM sources WHERE id=?", (args.source_id,)
        ).fetchone()
        if row is None:
            print(f"no such source: {args.source_id}", file=sys.stderr)
            return 1
        preset_id = row["court_preset_id"]
        if not preset_id:
            print("no --preset given and none assigned; see `splitstep preset list`",
                  file=sys.stderr)
            return 1
    # Captured before this invocation queues anything, so the --now check
    # below only ever looks at jobs THIS run created -- get_failed_jobs_for_source
    # is otherwise unscoped, and a source that failed once in a completely
    # unrelated earlier run would make every later, successful `setup --now`
    # exit non-zero forever.
    since = datetime.now(UTC).isoformat()
    try:
        job_id = queue_setup(conn, args.source_id, args.rotation, preset_id)
    except (ValueError, LookupError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"queued build_proxy {job_id}")
    if args.now:
        # Two run_once calls: build_proxy, then the detect it enqueued.
        Worker(lib, HANDLERS).run_once()
        Worker(lib, HANDLERS).run_once()
        # Check if any jobs for this source failed. If so, print the error
        # and return non-zero so the caller knows the rebuild didn't succeed.
        failed_jobs = jobq.get_failed_jobs_for_source(conn, args.source_id, since=since)
        for job in failed_jobs:
            if job["error"]:
                print(job["error"], file=sys.stderr)
        if failed_jobs:
            return 1
    return 0


def cmd_serve(args) -> int:
    if args.create and args.library is None:
        # --create builds a library. A path resolved from SPLITSTEP_LIBRARY or
        # the config file was chosen in some earlier session, not this one --
        # letting --create act on it would bypass exactly the guard
        # Library.open() exists to enforce: an unclean-ejected drive leaves an
        # empty mountpoint that passes is_dir()/os.access(), and open_or_create
        # would read that as "nothing here yet, make one" and silently start a
        # second library where the real one used to be mounted. Requiring the
        # flag explicitly means creation only ever targets a path a human (or
        # the Tauri picker) chose in this invocation. Checked first, before
        # any Library call, so this never touches disk or starts a server.
        print("error: --create requires an explicit --library <path>", file=sys.stderr)
        return 2

    import uvicorn

    from splitstep.api.app import create_app

    root = appconfig.resolve_library(args.library)
    # --create is the app's first-run path: the folder was just picked by a
    # human, so initializing it is the intent. Everything else keeps open()'s
    # strict guard.
    lib = Library.open_or_create(root) if args.create else Library.open(root)
    app = create_app(lib, spa_dist=resources.spa_dist())

    # Before anything else touches clips/: a still-flat legacy clip is
    # exactly what reconcile_clip_layout's docstring warns both of its
    # downstream readers about. The server process runs plan_export on every
    # `clips export` request, where an un-swept clip reads as "not cut" and
    # queues a pointless re-encode -- but it's just as reachable through
    # find_orphan_clips/delete_orphan_clips, whose `claimed` set is built
    # from nested relpaths, so a still-flat clip a live rally references
    # would read as an orphan and `clips prune --yes` would delete footage
    # nothing else can regenerate. A short-lived connection, same shape as
    # cmd_doctor's, closed immediately after: the worker and watcher get
    # their own connections once they start.
    conn = connect(lib.db_path)
    migrate(conn)
    reconcile_clip_layout(lib, conn)
    conn.close()

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
    params = params_for_frames(frames, threshold=args.threshold)
    intervals = segment(frames, params)

    if args.dry_run:
        for i, iv in enumerate(intervals, 1):
            print(f"{i:3d}  {_format_ts(iv.start_ms):>9} → {_format_ts(iv.end_ms):>9}"
                  f"  ({(iv.end_ms-iv.start_ms)/1000:5.1f}s)  conf {iv.confidence:.2f}")
        print(f"\n{len(intervals)} rallies at threshold {params.threshold}")
        return 0

    replace_rallies(conn, source["session_id"], args.source_id, intervals)
    # Mirrors api_resegment: replace_rallies inserts every new rally with
    # reviewed_at NULL, so a session that read 'reviewed' before this call now
    # contains nothing anyone has seen. Without the refresh the Library keeps
    # showing it as done and never prompts for the new rallies. HTTP and
    # terminal must not drift here, the same way setup.py::queue_setup keeps
    # them from drifting on validation.
    refresh_session_review_status(conn, source["session_id"])
    print(f"wrote {len(intervals)} rallies")
    print(json.dumps([dict(r) for r in list_rallies(conn, source["session_id"])],
                     indent=2)[:2000])
    return 0


def _source_or_fail(conn, source_id: str):
    source = get_source(conn, source_id)
    if source is None:
        print(f"source not found: {source_id}", file=sys.stderr)
        return None
    return source


def cmd_labels_export(args) -> int:
    library = _library(args)
    conn = connect(library.db_path)
    migrate(conn)
    source = _source_or_fail(conn, args.source_id)
    if source is None:
        return 1

    rows = latest_labels(conn, args.source_id)
    payload = {
        # Library-relative, matching labels_2026-08-18_source01.json's own
        # "source" field -- an absolute path would be meaningless once the
        # export is committed and read on another machine.
        "source": str(
            library.source_dir(source["session_id"], source["idx"]).relative_to(library.root)
        ),
        "source_id": args.source_id,
        "exported_on": datetime.now(UTC).date().isoformat(),
        "labels": [
            {
                "span_start_ms": r["span_start_ms"],
                "span_end_ms": r["span_end_ms"],
                "verdict": r["verdict"],
                "boundary_flags": parse_flags(r["boundary_flags"]),
                "true_start_ms": r["true_start_ms"],
                "true_end_ms": r["true_end_ms"],
            }
            for r in rows
        ],
    }
    text = json.dumps(payload, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n")
        print(f"wrote {len(payload['labels'])} labels to {args.out}")
    else:
        print(text)
    return 0


def _fmt_ms(v: float | None) -> str:
    return "--" if v is None else f"{v:+.0f} ms"


def _fmt_pct(v: float | None) -> str:
    return "--" if v is None else f"{v * 100:.0f}%"


def cmd_labels_score(args) -> int:
    library = _library(args)
    conn = connect(library.db_path)
    migrate(conn)
    source = _source_or_fail(conn, args.source_id)
    if source is None:
        return 1

    path = library.source_dir(source["session_id"], source["idx"]) / "features.jsonl"
    if not path.exists():
        print(f"source has not been detected yet: {args.source_id}", file=sys.stderr)
        return 1

    frames = read_features(path)
    params = params_for_frames(frames, threshold=args.threshold)
    intervals = segment(frames, params)
    labels = rows_to_labels(latest_labels(conn, args.source_id))
    score = score_against_labels(intervals, labels)

    print(f"{len(intervals)} candidate rallies at threshold {params.threshold:.2f}"
          f" against {len(labels)} labelled spans")
    print(f"  precision                        {_fmt_pct(score.precision)}"
          f"  ({score.matched_play} play / {score.matched_play + score.matched_not_play} decided)")
    print(f"  span recall (labelled spans only) {_fmt_pct(score.span_recall)}"
          f"  ({score.labelled_clean - score.missed_clean} of {score.labelled_clean} clean)")
    # The unbiased figure, and the only one on this page that means what the
    # word recall ordinarily means -- blind-sampled windows were chosen from
    # the whole source, not from what the detector happened to flag. Printed
    # even when it is empty, and saying plainly that it is empty: silence
    # would read as "nothing to report here" rather than "nobody measured".
    if score.sampled_clean == 0:
        print("  sampled recall (blind windows)    —"
              "       (no blind windows labelled yet)")
    else:
        print(f"  sampled recall (blind windows)   {_fmt_pct(score.sampled_recall)}"
              f"  ({score.sampled_clean - score.missed_sampled_clean}"
              f" of {score.sampled_clean} clean)")
    print(f"  unknown                          {score.unknown}"
          "  (candidates matching no label)")
    print(f"  start bias / MAE                 {_fmt_ms(score.start_bias_ms)}"
          f" / {_fmt_ms(score.start_mae_ms)}   (n={score.boundary_n})")
    print(f"  end bias / MAE                   {_fmt_ms(score.end_bias_ms)}"
          f" / {_fmt_ms(score.end_mae_ms)}")
    # Printed every run, not as a footnote in the docs. Every label in the
    # corpus attaches to a span the detector proposed, so this figure cannot
    # see play the detector never proposed -- and a number called plain
    # "recall" here would repeat exactly the mistake that let audio-impact
    # clustering stand in as ground truth.
    print("\n  span recall cannot see play the detector never proposed:"
          " every label sits on a span it did.")
    if score.sampled_clean == 0:
        print("  `splitstep labels sample` draws windows it did not, which is"
              " what sampled recall measures.")
    return 0


def cmd_labels_sample(args) -> int:
    """Print a blind set of windows to judge, half flagged and half ignored.

    The terminal view of what the audit route in the app walks through. It
    exists mostly so a sample can be eyeballed (or diffed across seeds)
    without opening the app, and deliberately prints the windows in the same
    shuffled order the API serves them: sorting here would leak, by position,
    which ones the detector flagged.
    """
    library = _library(args)
    conn = connect(library.db_path)
    migrate(conn)
    source = _source_or_fail(conn, args.source_id)
    if source is None:
        return 1

    intervals = [
        (r["det_start_ms"], r["det_end_ms"])
        for r in list_rallies_for_source(conn, args.source_id)
        if r["det_start_ms"] is not None
    ]
    windows = sample_windows(
        duration_ms=source["duration_ms"],
        intervals=intervals,
        n=args.n,
        seed=args.seed,
        window_ms=args.window_ms,
    )
    print(f"{len(windows)} windows of {args.window_ms} ms, seed {args.seed}"
          f" ({len(intervals)} detector intervals on this source)")
    for i, w in enumerate(windows, start=1):
        print(f"  {i:>3}.  {_format_ts(w.start_ms):>9} --> {_format_ts(w.end_ms):>9}")
    return 0


def _session_or_fail(conn, session_id: str):
    row = get_session(conn, session_id)
    if row is None:
        print(f"session not found: {session_id}", file=sys.stderr)
    return row


def cmd_score_set(args) -> int:
    """Same validator as POST /api/sessions/{id}/scoring (set_scoring ->
    rules_from_dict), so HTTP and terminal cannot drift."""
    library = _library(args)
    conn = connect(library.db_path)
    migrate(conn)
    if _session_or_fail(conn, args.session_id) is None:
        return 1
    rules = {
        "players": list(args.players),
        "sets": args.sets,
        "ad": not args.no_ad,
        "tiebreak": args.tiebreak,
        "tiebreakTo": args.tiebreak_to,
    }
    set_scoring(conn, args.session_id, rules)
    print(f"tracking {args.players[0]} vs {args.players[1]}: best of {args.sets},"
          f" {'no-ad' if args.no_ad else 'ad'}, tiebreak {args.tiebreak}")
    return 0


def cmd_score_off(args) -> int:
    library = _library(args)
    conn = connect(library.db_path)
    migrate(conn)
    if _session_or_fail(conn, args.session_id) is None:
        return 1
    set_scoring(conn, args.session_id, None)
    print("score tracking off; winners kept")
    return 0


def cmd_score_show(args) -> int:
    library = _library(args)
    conn = connect(library.db_path)
    migrate(conn)
    session = _session_or_fail(conn, args.session_id)
    if session is None:
        return 1
    rules = scoring_rules(session)
    if rules is None:
        print("not tracking a score for this session")
        return 0
    parsed = rules_from_dict(rules)
    # An id no rally holds replays every scored point: the current score.
    state, unscored = score_before(
        [dict(r) for r in list_rallies(conn, args.session_id)], "", parsed
    )
    for row in scoreboard_rows(state, parsed):
        print("  ".join(f"{cell:>4}" if i else f"{cell:<12}" for i, cell in enumerate(row)))
    if unscored:
        print(f"({unscored} point{'s' if unscored != 1 else ''} with no winner)")
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


def cmd_clips_export(args) -> int:
    library = _library(args)
    conn = connect(library.db_path)
    migrate(conn)
    # Must run before plan_export in this process: a claimed flat clip that
    # has not moved yet would read as "not cut" and queue a pointless
    # re-encode. See reconcile_clip_layout's docstring.
    reconcile_clip_layout(library, conn)
    if get_session(conn, args.session_id) is None:
        print(f"session not found: {args.session_id}", file=sys.stderr)
        return 1

    plan = plan_export(library, conn, args.session_id, args.set)
    for payload in plan.pending:
        jobq.enqueue(conn, "clip", payload)
    print(f"queued {len(plan.pending)} clip job(s) for {args.set} in {args.session_id}")
    if not plan.pending:
        if plan.total == 0:
            # Distinct from every branch below: an empty set never had a
            # clip to begin with, so "already exists" (which implies one was
            # cut) would be a flat lie -- this is the case that prompted the
            # split in the first place.
            print(f"nothing to cut -- no rallies in the {args.set} set")
        elif plan.in_flight or plan.unavailable:
            # Same honesty problem the route had: a nonzero already_cut can
            # coexist with jobs still encoding or rallies whose source is
            # gone, and folding those into "already exists" would say every
            # clip is done when some are not, or never will be.
            print(f"nothing new to cut -- {plan.already_cut} already cut, "
                  f"{plan.in_flight} in flight, {plan.unavailable} unavailable")
        else:
            print("nothing to cut -- every clip in that set already exists")
    return 0


def _fmt_bytes(n: int) -> str:
    """Decimal MB/GB, the same units the drive's own free-space figure uses --
    a sweep that reports MiB against a Finder reading GB invites arithmetic
    the user should not have to do."""
    return f"{n / 1e9:.1f} GB" if n >= 1_000_000_000 else f"{n / 1e6:.1f} MB"


def _load_orphans(args):
    """(library, conn, orphans) for the session, or None if it does not exist.

    Shared by `clips orphans` and `clips prune` so the list a user is shown
    is produced by exactly the code that would delete it -- two separate
    walks could disagree, and the one that deletes must never see more than
    the one that reported.
    """
    library = _library(args)
    conn = connect(library.db_path)
    migrate(conn)
    # Must run before find_orphan_clips: `claimed` there is built from
    # nested relpaths, so a still-flat legacy clip a live rally references
    # would read as unclaimed -- an orphan `clips prune --yes` would delete,
    # even though a rally still holds that exact span. See
    # reconcile_clip_layout's docstring.
    reconcile_clip_layout(library, conn)
    if get_session(conn, args.session_id) is None:
        print(f"session not found: {args.session_id}", file=sys.stderr)
        return None
    return library, conn, find_orphan_clips(library, conn, args.session_id)


def _print_orphans(orphans) -> None:
    for orphan in orphans:
        print(f"  {orphan.path.name}  source {orphan.source_idx:02d}  "
              f"{_format_ts(orphan.start_ms)}-{_format_ts(orphan.end_ms)}  "
              f"{_fmt_bytes(orphan.size_bytes)}")


def cmd_clips_orphans(args) -> int:
    loaded = _load_orphans(args)
    if loaded is None:
        return 1
    _library, _conn, orphans = loaded
    if not orphans:
        print(f"no orphan clips in {args.session_id}")
        return 0
    total = sum(o.size_bytes for o in orphans)
    print(f"{len(orphans)} orphan clip(s) in {args.session_id}, {_fmt_bytes(total)} total:")
    _print_orphans(orphans)
    print("delete them with: clips prune <session_id> --yes")
    return 0


def cmd_clips_prune(args) -> int:
    loaded = _load_orphans(args)
    if loaded is None:
        return 1
    library, conn, orphans = loaded
    if not orphans:
        print(f"no orphan clips in {args.session_id}")
        return 0

    total = sum(o.size_bytes for o in orphans)
    if not args.yes:
        # Dry run by default. A deleted clip costs a four-to-eight-minute
        # re-encode to get back, and unlike a re-segment there is no undo --
        # so the destructive reading of this command has to be the one the
        # user typed on purpose.
        print(f"would delete {len(orphans)} orphan clip(s), {_fmt_bytes(total)}:")
        _print_orphans(orphans)
        print("nothing deleted -- re-run with --yes")
        return 0

    deleted = delete_orphan_clips(library, conn, orphans)
    print(f"deleted {deleted} orphan clip(s), {_fmt_bytes(total)} reclaimed")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(prog="splitstep")
    parser.add_argument("--library", help="path to the library root")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create a new library tree and database")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("doctor", help="show detected hardware and library state")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("config", help="show or set persistent configuration")
    config_sub = p.add_subparsers(dest="config_command", required=True)

    cs = config_sub.add_parser("show", help="print the config file and resolved values")
    cs.set_defaults(func=cmd_config_show)

    cl = config_sub.add_parser("set-library", help="remember a default library path")
    cl.add_argument("path")
    cl.set_defaults(func=cmd_config_set_library)

    ch = config_sub.add_parser(
        "set-hr-clips", help="point this library at RallyMetrics's clips folder (or 'none')"
    )
    ch.add_argument("path")
    ch.set_defaults(func=cmd_config_set_hr_clips)

    p = sub.add_parser("serve", help="run the web server, worker and inbox watcher")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8420)
    p.add_argument("--create", action="store_true",
                   help="initialize the library first if the directory is empty")
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

    p = sub.add_parser("setup", help="set a source's rotation and play region, then rebuild")
    p.add_argument("source_id")
    p.add_argument("--rotation", type=int, required=True, help="0, 90, 180 or 270 (clockwise)")
    p.add_argument("--preset", help="court preset id; defaults to the one already assigned")
    p.add_argument("--now", action="store_true", help="run the jobs inline instead of queueing")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("segment", help="re-segment cached features")
    p.add_argument("source_id")
    p.add_argument("--threshold", type=float, default=None,
                   help="override the profile's default threshold")
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

    p = sub.add_parser("labels", help="export or score the human label corpus")
    labels_sub = p.add_subparsers(dest="labels_cmd", required=True)

    le = labels_sub.add_parser("export", help="write a source's labels as JSON")
    le.add_argument("source_id")
    le.add_argument("--out", help="write to this path instead of stdout")
    le.set_defaults(func=cmd_labels_export)

    ls = labels_sub.add_parser("score", help="score a segmentation against the labels")
    ls.add_argument("source_id")
    ls.add_argument("--threshold", type=float, default=None,
                    help="override the profile's default score threshold")
    ls.set_defaults(func=cmd_labels_score)

    lsa = labels_sub.add_parser(
        "sample", help="draw blind windows to label, including ones the detector ignored"
    )
    lsa.add_argument("source_id")
    lsa.add_argument("--n", type=int, default=20, help="how many windows to draw")
    lsa.add_argument("--seed", type=int, default=0,
                     help="the same seed redraws the same sample")
    lsa.add_argument("--window-ms", type=int, default=DEFAULT_WINDOW_MS,
                     dest="window_ms", help="length of each window")
    lsa.set_defaults(func=cmd_labels_sample)

    p = sub.add_parser("score", help="match-score tracking for a session")
    score_sub = p.add_subparsers(dest="score_cmd", required=True)

    ss = score_sub.add_parser("set", help="turn tracking on (or change the rules)")
    ss.add_argument("session_id")
    ss.add_argument("--players", nargs=2, metavar=("A", "B"),
                    default=list(DEFAULT_RULES.players))
    ss.add_argument("--sets", type=int, choices=(1, 3, 5), default=DEFAULT_RULES.sets)
    ss.add_argument("--no-ad", action="store_true", help="sudden death at deuce")
    ss.add_argument("--tiebreak", choices=("at6", "none", "only"), default=DEFAULT_RULES.tiebreak)
    ss.add_argument("--tiebreak-to", type=int, choices=(7, 10), default=DEFAULT_RULES.tiebreak_to)
    ss.set_defaults(func=cmd_score_set)

    so = score_sub.add_parser("off", help="stop tracking; winners are kept")
    so.add_argument("session_id")
    so.set_defaults(func=cmd_score_off)

    sw = score_sub.add_parser("show", help="print the current board")
    sw.add_argument("session_id")
    sw.set_defaults(func=cmd_score_show)

    p = sub.add_parser("clips", help="cut clips from a session's rallies")
    clips_sub = p.add_subparsers(dest="clips_command", required=True)

    ce = clips_sub.add_parser("export", help="queue clip jobs for a session's points or stars")
    ce.add_argument("session_id")
    ce.add_argument("--set", choices=SETS, default="points")
    ce.set_defaults(func=cmd_clips_export)

    co = clips_sub.add_parser("orphans", help="list clips no rally's current bounds claim")
    co.add_argument("session_id")
    co.set_defaults(func=cmd_clips_orphans)

    cp = clips_sub.add_parser("prune", help="delete orphaned clips (dry run without --yes)")
    cp.add_argument("session_id")
    cp.add_argument("--yes", action="store_true",
                    help="actually delete; without it the orphans are only listed")
    cp.set_defaults(func=cmd_clips_prune)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except LibraryNotMounted as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except LibraryAlreadyInitialized as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except appconfig.LibraryUnconfigured as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
