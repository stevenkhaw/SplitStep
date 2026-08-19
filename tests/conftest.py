import pytest

from bootleg.config import Library


@pytest.fixture
def library(tmp_path) -> Library:
    for sub in ("_inbox", "sessions", "reels"):
        (tmp_path / sub).mkdir()
    return Library.open(tmp_path)
