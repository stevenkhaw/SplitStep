import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from splitstep.config import Library
from splitstep.db.presets import create_preset
from splitstep.db.schema import connect, migrate
from splitstep.detect.features import FeatureFrame, read_features
from splitstep.detect.geometry import Quad
from splitstep.jobs.handlers import handle_ingest

FIXTURES = Path(__file__).parent / "fixtures"


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
def hlg_setparams() -> str:
    """The `setparams` filter that tags a synthetic source as the locked profile.

    Every source fixture in this suite needs it, because `make_clip` refuses
    a source whose colour metadata is not the profile's -- and lavfi output
    carries none at all.

    It has to be a filter, not the -color_range/-colorspace/-color_primaries
    /-color_trc output flags. Measured on ffmpeg 9.0.1: with a lavfi input
    those flags write the matrix and the range and silently DROP primaries
    and transfer, yielding a file that reports `unknown` for two of the four
    fields. A fixture built that way would look right in the command line,
    pass a careless assertion, and misrepresent what make_clip sees. With a
    real file as input the flags do stick, which is why make_clip itself
    uses them and only the fixtures need this.

    Returned bare so a caller with an existing -vf can comma-join onto it.
    """
    return "setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc:range=tv"


@pytest.fixture
def sample_video(tmp_path, hlg_setparams):
    """2 second 320x240 30fps clip with a 440Hz tone, tagged as the locked profile."""
    out = tmp_path / "sample.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-vf", hlg_setparams,
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


@pytest.fixture(scope="session")
def ground_features() -> list[FeatureFrame]:
    """A 4-minute slice of real ground-level footage (source 01, t=200-440 s).

    Committed as source, not generated: every tuning constant in the detector
    was previously fitted against synthetic streams that hand every player
    v=2.0 -- roughly 8x anything real -- and that is precisely what produced
    the one-hit-per-clip bug this module exists to fix. Assertions that matter
    are made against this file, not against hand-written frames.
    """
    return read_features(FIXTURES / "ground_level_source01.jsonl")
