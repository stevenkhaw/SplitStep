import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from splitstep import appconfig, cli
from splitstep.cli import _format_ts, main
from splitstep.db.rallies import list_rallies
from splitstep.db.schema import connect, migrate
from splitstep.db.sessions import (
    add_source,
    find_or_create_session_for_date,
    set_session_status,
)
from splitstep.detect.features import FeatureFrame, Player, write_features
from splitstep.detect.segment import SegmentParams


@pytest.fixture
def conn(library):
    c = connect(library.db_path)
    migrate(c)
    return c


@pytest.fixture
def seeded_source(library, conn):
    """A source with cached features that segment to exactly one rally.

    Same 8 s two-player-active stream used in tests/test_handlers.py — no
    YOLO or ffmpeg needed, segment() runs on the cached features alone.
    """
    session_id = find_or_create_session_for_date(conn, "2026-08-19")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-19T10:00:00Z", duration_ms=8000,
        width=1920, height=1080, fps=30.0, original_name="IMG_0001.MOV",
    )
    frames = [
        FeatureFrame(i * 200, 2,
                     Player(0.5, 0.9, 0.30, 2.5), Player(0.5, 0.4, 0.10, 2.5),
                     hits=1, hit_reg=0.9)
        for i in range(40)
    ]
    write_features(library.source_dir(session_id, idx) / "features.jsonl", frames)
    return {"session_id": session_id, "source_id": source_id, "idx": idx}


# -- _format_ts --------------------------------------------------------------

def test_format_ts_sub_minute():
    assert _format_ts(12_400) == "0:12.4"


def test_format_ts_past_one_minute():
    assert _format_ts(75_300) == "1:15.3"


def test_format_ts_past_one_hour():
    assert _format_ts(3_661_200) == "1:01:01.2"


def test_format_ts_zero():
    assert _format_ts(0) == "0:00.0"


def test_format_ts_rounds_up_seconds_into_the_next_minute():
    """59.95s must carry into the minute, not print an invalid '0:60.0'.

    This is the boundary the module docstring calls out: naive float
    formatting of seconds to one decimal place can round 59.95 up to
    "60.0" instead of rolling over -- exactly the kind of thing that
    silently breaks at the hour boundary if reintroduced.
    """
    assert _format_ts(59_950) == "1:00.0"


# -- doctor --------------------------------------------------------------

def test_doctor_on_valid_library_returns_0(library, capsys):
    rc = main(["--library", str(library.root), "doctor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert str(library.root) in out
    assert "hwaccel:" in out
    assert "torch device:" in out
    assert out.count("ok ") == 3  # inbox, sessions, reels all exist


def test_doctor_on_missing_library_returns_2_and_does_not_raise(tmp_path, capsys):
    missing = tmp_path / "not-mounted"
    rc = main(["--library", str(missing), "doctor"])
    assert rc == 2
    err = capsys.readouterr().err
    assert f"Library root not found: {missing}" in err
    assert "Is the drive plugged in?" in err


def test_doctor_on_an_uninitialized_directory_returns_2(tmp_path, capsys):
    """A directory that exists but was never `splitstep init`-ed (the leftover
    mountpoint case) must not be silently treated as a fresh empty library.
    """
    tmp_path.mkdir(exist_ok=True)  # tmp_path already exists; this is a no-op
    rc = main(["--library", str(tmp_path), "doctor"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "library.db" in err
    assert not (tmp_path / "library.db").exists()


# -- init --------------------------------------------------------------------

def test_init_creates_the_tree_and_database(tmp_path, capsys):
    rc = main(["--library", str(tmp_path), "init"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "initialized library" in out
    assert (tmp_path / "library.db").exists()
    assert (tmp_path / "_inbox").is_dir()
    assert (tmp_path / "sessions").is_dir()
    assert (tmp_path / "reels").is_dir()

    # And the library it just created is now usable.
    rc = main(["--library", str(tmp_path), "doctor"])
    assert rc == 0


def test_init_refuses_to_clobber_an_existing_library(library, capsys):
    rc = main(["--library", str(library.root), "init"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "already initialized" in err


def test_init_on_an_unmounted_path_returns_2(tmp_path, capsys):
    missing = tmp_path / "not-plugged-in"
    rc = main(["--library", str(missing), "init"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Is the drive plugged in?" in err
    assert not missing.exists()


# -- segment ---------------------------------------------------------------

def test_segment_unknown_source_returns_1(library, capsys):
    rc = main(["--library", str(library.root), "segment", "no-such-source", "--dry-run"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no such source: no-such-source" in err


def test_segment_dry_run_prints_intervals_and_writes_no_rallies(
    library, conn, seeded_source, capsys
):
    rc = main(["--library", str(library.root), "segment",
               seeded_source["source_id"], "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    # The fixture's one interval spans 0 -- 8000 ms; printed as M:SS.s timestamps,
    # not raw seconds -- this is what a user reads against their memory of the match.
    assert "0:00.0" in out
    assert "0:08.0" in out
    assert "8.0s" in out  # duration column stays plain seconds
    assert "conf" in out
    # Read the default off SegmentParams rather than hardcoding it: this
    # assertion is about the summary line's shape, not about which
    # threshold is currently calibrated, and a literal here failed the
    # suite for an unrelated retune.
    assert f"1 rallies at threshold {SegmentParams().threshold}" in out
    assert list_rallies(conn, seeded_source["session_id"]) == []


def test_segment_dry_run_reflects_threshold_argument(library, seeded_source, capsys):
    rc = main(["--library", str(library.root), "segment",
               seeded_source["source_id"], "--threshold", "0.99", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "0 rallies at threshold 0.99" in out


def test_segment_without_dry_run_writes_rallies(library, conn, seeded_source, capsys):
    rc = main(["--library", str(library.root), "segment", seeded_source["source_id"]])
    assert rc == 0
    out = capsys.readouterr().out
    assert "wrote 1 rallies" in out
    rows = list_rallies(conn, seeded_source["session_id"])
    assert len(rows) == 1


# -- ingest / detect (argparse wiring) --------------------------------------

def test_ingest_queues_a_job(library, conn, tmp_path, capsys):
    fake_video = tmp_path / "clip.mov"
    rc = main(["--library", str(library.root), "ingest", str(fake_video)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "queued ingest" in out
    row = conn.execute("SELECT * FROM jobs WHERE type='ingest'").fetchone()
    assert row is not None
    assert row["status"] == "queued"
    assert json.loads(row["payload"])["path"] == str(fake_video.resolve())


def test_detect_queues_a_job(library, conn, seeded_source):
    rc = main(["--library", str(library.root), "detect", seeded_source["source_id"]])
    assert rc == 0
    row = conn.execute("SELECT * FROM jobs WHERE type='detect'").fetchone()
    assert row is not None
    payload = json.loads(row["payload"])
    assert payload["source_id"] == seeded_source["source_id"]
    assert payload["reuse_features"] is False


# -- serve (argparse routing only — never starts a real server) ------------

def test_serve_subcommand_routes_to_cmd_serve(library, monkeypatch):
    calls = {}

    def fake_cmd_serve(args) -> int:
        calls["host"] = args.host
        calls["port"] = args.port
        return 0

    monkeypatch.setattr(cli, "cmd_serve", fake_cmd_serve)
    rc = main(["--library", str(library.root), "serve", "--port", "9000"])
    assert rc == 0
    assert calls == {"host": "127.0.0.1", "port": 9000}


def test_serve_create_without_explicit_library_flag_refuses(tmp_path, monkeypatch, capsys):
    # Deliberately does NOT monkeypatch cmd_serve: the guard runs before any
    # Library call or uvicorn.run, so this exercises the real function and
    # never starts a server. An env var stands in for "a path configured in
    # some earlier session" -- exactly what --create must not act on.
    empty_mount = tmp_path / "empty_mount"
    empty_mount.mkdir()
    monkeypatch.setenv(appconfig.ENV_VAR, str(empty_mount))
    rc = main(["serve", "--create"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--library" in err
    assert list(empty_mount.iterdir()) == []


# -- preset / source set-preset ---------------------------------------------

QUAD_ARG = "0.1,0.9 0.9,0.9 0.7,0.3 0.3,0.3"


def test_preset_add_creates_a_row_and_prints_its_id(library, conn, capsys):
    rc = main(["--library", str(library.root), "preset", "add",
               "--name", "backyard", "--quad", QUAD_ARG])
    assert rc == 0
    preset_id = capsys.readouterr().out.strip()
    row = conn.execute("SELECT * FROM court_presets WHERE id = ?", (preset_id,)).fetchone()
    assert row is not None
    assert row["name"] == "backyard"


def test_preset_add_rejects_a_malformed_quad(library, conn, capsys):
    rc = main(["--library", str(library.root), "preset", "add",
               "--name", "bad", "--quad", "0.1,0.9 0.9,0.9 0.7,0.3"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid --quad" in err
    assert conn.execute("SELECT COUNT(*) AS n FROM court_presets").fetchone()["n"] == 0


def test_preset_add_rejects_a_non_numeric_point(library, capsys):
    rc = main(["--library", str(library.root), "preset", "add",
               "--name", "bad", "--quad", "x,0.9 0.9,0.9 0.7,0.3 0.3,0.3"])
    assert rc == 2
    assert "invalid --quad" in capsys.readouterr().err


def test_preset_list_prints_id_name_and_points(library, capsys):
    main(["--library", str(library.root), "preset", "add",
         "--name", "backyard", "--quad", QUAD_ARG])
    capsys.readouterr()

    rc = main(["--library", str(library.root), "preset", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "backyard" in out
    assert "0.1000,0.9000" in out


def test_source_set_preset_assigns_the_preset_to_the_source(library, conn, seeded_source, capsys):
    main(["--library", str(library.root), "preset", "add",
         "--name", "backyard", "--quad", QUAD_ARG])
    preset_id = capsys.readouterr().out.strip()

    rc = main(["--library", str(library.root), "source", "set-preset",
               seeded_source["source_id"], preset_id])
    assert rc == 0
    row = conn.execute(
        "SELECT court_preset_id FROM sources WHERE id = ?", (seeded_source["source_id"],)
    ).fetchone()
    assert row["court_preset_id"] == preset_id


def test_source_set_preset_unknown_source_returns_1(library, capsys):
    main(["--library", str(library.root), "preset", "add",
         "--name", "backyard", "--quad", QUAD_ARG])
    preset_id = capsys.readouterr().out.strip()

    rc = main(["--library", str(library.root), "source", "set-preset",
               "no-such-source", preset_id])
    assert rc == 1
    assert "no such source" in capsys.readouterr().err


def test_source_set_preset_unknown_preset_returns_1(library, seeded_source, capsys):
    rc = main(["--library", str(library.root), "source", "set-preset",
               seeded_source["source_id"], "no-such-preset"])
    assert rc == 1
    assert "no such preset" in capsys.readouterr().err


# -- setup ---------------------------------------------------------------

def test_setup_command_queues_a_build(library, registered_source, a_preset, capsys):
    code = main([
        "--library", str(library.root), "setup", registered_source.id,
        "--rotation", "90", "--preset", a_preset,
    ])
    assert code == 0
    assert "queued build_proxy" in capsys.readouterr().out


def test_setup_command_rejects_a_bad_rotation(library, registered_source, a_preset, capsys):
    code = main([
        "--library", str(library.root), "setup", registered_source.id,
        "--rotation", "45", "--preset", a_preset,
    ])
    assert code == 1
    assert "0, 90, 180 or 270" in capsys.readouterr().err


def test_setup_unknown_source_no_preset(library, a_preset, capsys):
    """Unknown source without --preset should report the unknown source, not missing preset."""
    code = main([
        "--library", str(library.root), "setup", "no-such-source",
        "--rotation", "90",
    ])
    assert code == 1
    err = capsys.readouterr().err
    assert "no such source: no-such-source" in err
    # Must NOT say "no --preset given"
    assert "no --preset given" not in err


def test_setup_existing_source_no_assigned_preset(library, registered_source, capsys):
    """Existing source with no assigned preset should report missing preset, not unknown source."""
    code = main([
        "--library", str(library.root), "setup", registered_source.id,
        "--rotation", "90",
    ])
    assert code == 1
    err = capsys.readouterr().err
    assert "no --preset given and none assigned" in err
    # Must NOT say "no such source"
    assert "no such source" not in err


def test_setup_now_with_failing_job(library, registered_source, a_preset, capsys, monkeypatch):
    """--now should return non-zero if a queued job fails."""
    from splitstep.jobs import handlers

    # Make make_proxy raise to simulate a job failure
    def failing_make_proxy(*args, **kwargs):
        raise RuntimeError("simulated build_proxy failure")

    monkeypatch.setattr(handlers, "make_proxy", failing_make_proxy)

    code = main([
        "--library", str(library.root), "setup", registered_source.id,
        "--rotation", "90", "--preset", a_preset, "--now",
    ])
    assert code != 0
    err = capsys.readouterr().err
    # Error from the failed job should appear in stderr
    assert "simulated build_proxy failure" in err


def test_setup_now_succeeds_despite_an_earlier_unrelated_failed_job(
    library, conn, registered_source, a_preset, capsys
):
    """Finding: get_failed_jobs_for_source is not time-scoped, so `setup
    --now` reported failure -- and exited non-zero -- if the source EVER
    had a failed job, even a stale one from a completely unrelated earlier
    run that has no bearing on whether THIS run's jobs succeeded. Seed a
    failed job row that predates this invocation, then run a `setup --now`
    that succeeds outright (no monkeypatched failure): the stale row must
    not be able to fail it.
    """
    old_ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    conn.execute(
        "INSERT INTO jobs (id,type,payload,status,error,created_at,finished_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            uuid.uuid4().hex, "build_proxy",
            json.dumps({"source_id": registered_source.id}),
            "failed", "stale unrelated failure from an earlier run",
            old_ts, old_ts,
        ),
    )
    conn.commit()

    code = main([
        "--library", str(library.root), "setup", registered_source.id,
        "--rotation", "90", "--preset", a_preset, "--now",
    ])
    assert code == 0
    assert "stale unrelated failure" not in capsys.readouterr().err


def test_doctor_lists_source_rotation(library, registered_source, capsys):
    main(["--library", str(library.root), "doctor"])
    out = capsys.readouterr().out
    assert "rotation" in out


def test_segment_leaves_the_session_review_status_consistent(
    library, conn, seeded_source, capsys
):
    """Re-segmenting must not leave a session claiming to be 'reviewed'.

    `replace_rallies` inserts every new rally with `reviewed_at` NULL, so a
    session that read 'reviewed' before the call has nothing seen in it
    afterwards. The API route already refreshes the status for exactly this
    reason (see the comment in api/routes.py::api_resegment); the CLI has
    never done so, which let `splitstep segment` strand a session showing
    'reviewed' with a full set of never-seen rallies -- invisible in the
    Library, so the user is never prompted to review them.

    HTTP and terminal must not drift here, the same way `setup.py::queue_setup`
    keeps them from drifting on validation.
    """
    set_session_status(conn, seeded_source["session_id"], "reviewed")

    rc = main(["--library", str(library.root), "segment", seeded_source["source_id"]])
    assert rc == 0

    row = conn.execute(
        "SELECT status FROM sessions WHERE id = ?", (seeded_source["session_id"],)
    ).fetchone()
    assert row["status"] == "ready", (
        "segment wrote a fresh set of unreviewed rallies but left the session "
        "marked 'reviewed'"
    )


def test_labels_export_writes_the_corpus_as_json(library, conn, capsys, tmp_path):
    # json, main, add_source, find_or_create_session_for_date and
    # write_features are already imported at the top of this file.
    from splitstep.db.labels import add_label
    from splitstep.db.rallies import replace_rallies
    from splitstep.detect.segment import Interval

    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=1920, height=1080, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id, [Interval(1000, 5000, 0.8)])
    add_label(conn, source_id=source_id, span_start_ms=1000, span_end_ms=5000,
              verdict="clean", boundary_flags=["end_late"])
    conn.close()

    out = tmp_path / "labels.json"
    rc = main(["--library", str(library.root), "labels", "export", source_id,
               "--out", str(out)])
    assert rc == 0

    payload = json.loads(out.read_text())
    assert payload["source_id"] == source_id
    assert payload["source"] == "sessions/2026-08-18/sources/01"
    assert payload["labels"] == [{
        "span_start_ms": 1000, "span_end_ms": 5000, "verdict": "clean",
        "boundary_flags": ["end_late"], "true_start_ms": None, "true_end_ms": None,
    }]


def test_labels_export_omits_a_retracted_span(library, conn, capsys, tmp_path):
    # The export is the corpus as it currently stands, not its history: a
    # span whose verdict the reviewer took back carries no judgement, and
    # writing it out as a null-verdict entry would put a span nobody judges
    # into a committed fixture where the scorer would count it as covered.
    from splitstep.db.labels import add_label, retract_label

    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=1920, height=1080, fps=30.0, original_name="IMG_9000.MOV",
    )
    add_label(conn, source_id=source_id, span_start_ms=1000, span_end_ms=5000,
              verdict="clean")
    add_label(conn, source_id=source_id, span_start_ms=9000, span_end_ms=14000,
              verdict="not_play")
    retract_label(conn, source_id=source_id, span_start_ms=1000, span_end_ms=5000,
                  rally_id=None)
    conn.close()

    out = tmp_path / "labels.json"
    assert main(["--library", str(library.root), "labels", "export", source_id,
                 "--out", str(out)]) == 0

    payload = json.loads(out.read_text())
    assert [lab["span_start_ms"] for lab in payload["labels"]] == [9000]


def test_labels_export_on_an_unknown_source_fails(library, conn, capsys):
    conn.close()
    rc = main(["--library", str(library.root), "labels", "export", "nope"])
    assert rc == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_labels_score_reports_the_recall_caveat_and_the_unknown_count(
    library, conn, capsys, ground_features
):
    """The two output constraints the spec makes non-negotiable.

    A bare "recall" would repeat the error that cost the last validation
    round, and a precision figure with the unknown count hidden conceals a
    sweep that matched three candidates and missed forty.
    """
    from splitstep.db.labels import add_label

    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=1920, height=1080, fps=30.0, original_name="IMG_9000.MOV",
    )
    add_label(conn, source_id=source_id, span_start_ms=1000, span_end_ms=5000,
              verdict="not_play")
    conn.close()

    src_dir = library.source_dir(session_id, idx)
    src_dir.mkdir(parents=True, exist_ok=True)
    write_features(src_dir / "features.jsonl", ground_features)

    rc = main(["--library", str(library.root), "labels", "score", source_id])
    assert rc == 0
    out = capsys.readouterr().out
    assert "span recall (labelled spans only)" in out
    assert "cannot see play the detector never proposed" in out
    assert "unknown" in out


def test_labels_score_without_features_fails(library, conn, capsys):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=1920, height=1080, fps=30.0, original_name="IMG_9000.MOV",
    )
    conn.close()

    rc = main(["--library", str(library.root), "labels", "score", source_id])
    assert rc == 1
    assert "not been detected" in capsys.readouterr().err.lower()


def test_clips_export_queues_a_job_per_point(library, conn, capsys):
    from splitstep.db.rallies import replace_rallies, set_point
    from splitstep.detect.segment import Interval

    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7)])
    for row in conn.execute("SELECT id FROM rallies").fetchall():
        set_point(conn, row["id"], True)
    conn.close()

    rc = main(["--library", str(library.root), "clips", "export", session_id])
    assert rc == 0
    assert "queued 2 clip job(s)" in capsys.readouterr().out

    c = connect(library.db_path)
    assert c.execute("SELECT COUNT(*) FROM jobs WHERE type='clip'").fetchone()[0] == 2


def test_clips_export_a_second_time_queues_nothing_and_says_so(library, conn, capsys):
    from splitstep.db.rallies import replace_rallies, set_point
    from splitstep.detect.segment import Interval

    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id, [Interval(1000, 5000, 0.8)])
    set_point(conn, conn.execute("SELECT id FROM rallies").fetchone()["id"], True)
    conn.close()

    main(["--library", str(library.root), "clips", "export", session_id])
    capsys.readouterr()
    # The job from the first run is still queued, so the span is in flight --
    # not cut -- and must not be enqueued twice, and must not be reported as
    # "already exists" (it doesn't, yet).
    rc = main(["--library", str(library.root), "clips", "export", session_id])
    assert rc == 0
    out = capsys.readouterr().out
    assert "queued 0 clip job(s)" in out
    assert "in flight" in out


def test_clips_export_on_an_unknown_session_fails(library, conn, capsys):
    conn.close()
    rc = main(["--library", str(library.root), "clips", "export", "nope"])
    assert rc == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_clips_export_on_a_genuinely_empty_set_says_so(library, conn, capsys):
    # No rally in the session is starred at all -- unlike the "already cut"
    # and "in flight" cases below, this set never had anything to cut, so
    # the message must not claim clips exist that were never queued.
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    conn.close()

    rc = main(["--library", str(library.root), "clips", "export", session_id, "--set", "starred"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "queued 0 clip job(s)" in out
    assert "no rallies in the starred set" in out
    assert "already exists" not in out
    assert "already cut" not in out


def test_clips_export_when_everything_is_unavailable_says_so(library, conn, capsys):
    # The one rally in the set points at a source that no longer exists --
    # different from "in flight" and from a genuinely empty set, and the
    # message must say which of the three it actually is.
    from splitstep.db.rallies import replace_rallies, set_point
    from splitstep.detect.segment import Interval

    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id, [Interval(1000, 5000, 0.8)])
    set_point(conn, conn.execute("SELECT id FROM rallies").fetchone()["id"], True)

    # rallies.source_id is ON DELETE CASCADE, so the ordinary path to a
    # vanished source would take the rally down with it. Toggle the pragma
    # off for this one delete to get the row shape the guard defends against
    # -- a rally whose source really is gone -- without losing the rally.
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    conn.close()

    rc = main(["--library", str(library.root), "clips", "export", session_id])
    assert rc == 0
    out = capsys.readouterr().out
    assert "queued 0 clip job(s)" in out
    assert "1 unavailable" in out
    assert "no rallies in the points set" not in out
    assert "already exists" not in out


# -- clips orphans / clips prune --------------------------------------------


@pytest.fixture
def stranded(library, conn):
    """A session whose re-segment left one clip behind.

    Three clips cut, then a sweep moves the middle span: the file at the old
    bounds is what nothing enumerates and nothing removes.
    """
    from splitstep.db.rallies import replace_rallies
    from splitstep.detect.segment import Interval
    from splitstep.media.clips import clip_relpath

    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    spans = [(1000, 5000), (9000, 14000), (20000, 26000)]
    replace_rallies(conn, session_id, source_id, [Interval(a, b, 0.8) for a, b in spans])

    clips_dir = library.clips_dir(session_id)
    clips_dir.mkdir(parents=True, exist_ok=True)
    for start_ms, end_ms in spans:
        (clips_dir / clip_relpath(idx, start_ms, end_ms)).write_bytes(b"x" * 1_000_000)

    replace_rallies(conn, session_id, source_id, [
        Interval(1000, 5000, 0.8), Interval(9200, 13800, 0.7), Interval(20000, 26000, 0.6),
    ])
    orphan = clips_dir / clip_relpath(idx, 9000, 14000)
    conn.close()
    return {"session_id": session_id, "orphan": orphan, "clips_dir": clips_dir}


def test_clips_orphans_names_the_stranded_file_and_its_size(library, stranded, capsys):
    rc = main(["--library", str(library.root), "clips", "orphans", stranded["session_id"]])
    assert rc == 0
    out = capsys.readouterr().out
    assert stranded["orphan"].name in out
    assert "1.0 MB" in out


def test_clips_orphans_deletes_nothing(library, stranded, capsys):
    """Listing is listing. Deleting a clip costs four to eight minutes of
    re-encode to get back, so nothing removes one as a side effect of being
    asked what is there."""
    main(["--library", str(library.root), "clips", "orphans", stranded["session_id"]])
    assert stranded["orphan"].exists()


def test_clips_orphans_on_a_clean_session_says_so(library, stranded, capsys):
    stranded["orphan"].unlink()
    rc = main(["--library", str(library.root), "clips", "orphans", stranded["session_id"]])
    assert rc == 0
    assert "no orphan" in capsys.readouterr().out.lower()


def test_clips_prune_without_yes_refuses_and_keeps_the_file(library, stranded, capsys):
    """Explicit rather than automatic, and the dry run is the default: a
    prune that deleted on sight would make `--help` exploration expensive."""
    rc = main(["--library", str(library.root), "clips", "prune", stranded["session_id"]])
    assert rc == 0
    assert stranded["orphan"].exists()
    out = capsys.readouterr().out
    assert "--yes" in out
    assert stranded["orphan"].name in out


def test_clips_prune_with_yes_deletes_the_orphan(library, stranded, capsys):
    rc = main(["--library", str(library.root), "clips", "prune",
               stranded["session_id"], "--yes"])
    assert rc == 0
    assert not stranded["orphan"].exists()
    assert "deleted 1" in capsys.readouterr().out.lower()


def test_clips_prune_leaves_the_clips_a_rally_still_claims(library, stranded, capsys):
    main(["--library", str(library.root), "clips", "prune", stranded["session_id"], "--yes"])
    survivors = sorted(p.name for p in stranded["clips_dir"].iterdir())
    assert survivors == ["01-1000-5000.mp4", "01-20000-26000.mp4"]


def test_clips_orphans_on_an_unknown_session_fails(library, conn, capsys):
    conn.close()
    rc = main(["--library", str(library.root), "clips", "orphans", "nope"])
    assert rc == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_clips_prune_on_an_unknown_session_fails(library, conn, capsys):
    conn.close()
    rc = main(["--library", str(library.root), "clips", "prune", "nope", "--yes"])
    assert rc == 1
    assert "not found" in capsys.readouterr().err.lower()


# -- optional --library / config commands -------------------------------------

@pytest.fixture(autouse=True)
def isolated_appconfig(tmp_path, monkeypatch):
    # Same isolation as tests/test_appconfig.py: never touch the real config.
    monkeypatch.setattr(appconfig, "config_path", lambda: tmp_path / "appconfig.json")
    monkeypatch.delenv(appconfig.ENV_VAR, raising=False)


def test_library_flag_is_now_optional_and_env_var_works(library, monkeypatch, capsys):
    monkeypatch.setenv(appconfig.ENV_VAR, str(library.root))
    assert main(["doctor"]) == 0
    assert str(library.root) in capsys.readouterr().out


def test_no_library_anywhere_exits_2_with_guidance(capsys):
    assert main(["doctor"]) == 2
    err = capsys.readouterr().err
    assert "--library" in err and appconfig.ENV_VAR in err


def test_config_set_library_then_commands_need_no_flag(library, capsys):
    assert main(["config", "set-library", str(library.root)]) == 0
    assert main(["doctor"]) == 0


def test_config_set_library_refuses_a_missing_directory(tmp_path, capsys):
    assert main(["config", "set-library", str(tmp_path / "nope")]) == 1


def test_config_show_reports_unset(capsys):
    assert main(["config", "show"]) == 0
    assert "unset" in capsys.readouterr().out
