import pytest

from bootleg.config import Library
from bootleg.db.schema import connect, migrate


@pytest.fixture
def library(tmp_path) -> Library:
    for sub in ("_inbox", "sessions", "reels"):
        (tmp_path / sub).mkdir()
    conn = connect(tmp_path / "library.db")
    migrate(conn)
    conn.close()
    return Library.open(tmp_path)
