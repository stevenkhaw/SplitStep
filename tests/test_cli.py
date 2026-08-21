import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from bootleg import cli
from bootleg.cli import _format_ts, main
from bootleg.db.rallies import list_rallies
from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import (
    add_source,
    find_or_create_session_for_date,
    set_session_status,
)
from bootleg.detect.features import FeatureFrame, Player, write_features
from bootleg.detect.segment import SegmentParams


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
    """A directory that exists but was never `bootleg init`-ed (the leftover
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
    from bootleg.jobs import handlers

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
    never done so, which let `bootleg segment` strand a session showing
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
