import json

import pytest

from bootleg import cli
from bootleg.cli import main
from bootleg.db.rallies import list_rallies
from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import add_source, find_or_create_session_for_date
from bootleg.detect.features import FeatureFrame, Player, write_features


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
    assert "conf" in out
    assert "1 rallies at threshold 0.45" in out
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
