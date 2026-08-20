import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from bootleg.config import Library
from bootleg.db.presets import create_preset
from bootleg.db.schema import connect, migrate
from bootleg.detect.geometry import Quad
from bootleg.jobs.handlers import handle_ingest


@pytest.fixture
def library(tmp_path) -> Library:
    for sub in ("_inbox", "sessions", "reels"):
        (tmp_path / sub).mkdir()
    conn = connect(tmp_path / "library.db")
    migrate(conn)
    conn.close()
    return Library.open(tmp_path)


@pytest.fixture
def conn(library):
    c = connect(library.db_path)
    migrate(c)
    return c


@pytest.fixture
def sample_video(tmp_path):
    """2 second 320x240 30fps clip with a 440Hz tone."""
    out = tmp_path / "sample.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out)],
        check=True, capture_output=True,
    )
    return out


@dataclass
class RegisteredSource:
    session_id: str
    id: str
    idx: int
    dir: Path


@pytest.fixture
def registered_source(library, conn, sample_video):
    """A source the way the setup wizard finds it: `handle_ingest` has run,
    the original is on disk in the session tree, and no proxy exists yet
    (see handle_ingest's docstring -- both wait on the wizard). preview.jpg
    exists for exactly this pre-proxy state.
    """
    dropped = library.inbox / "IMG_9000.MOV"
    dropped.write_bytes(sample_video.read_bytes())
    handle_ingest(library, {"path": str(dropped)})
    row = conn.execute("SELECT * FROM sources").fetchone()
    return RegisteredSource(
        session_id=row["session_id"],
        id=row["id"],
        idx=row["idx"],
        dir=library.source_dir(row["session_id"], row["idx"]),
    )


@pytest.fixture
def a_preset(conn):
    quad = Quad(((0.1, 0.9), (0.9, 0.9), (0.7, 0.3), (0.3, 0.3)))
    return create_preset(conn, "test_court", quad)
