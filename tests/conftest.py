import sys

import pytest

# starlette.testclient prefers an `httpx2` package and only falls back to
# `httpx` behind a StarletteDeprecationWarning. `httpx2` isn't installed in
# this environment and shouldn't be pip-installed just to silence a warning,
# so alias the name to the real `httpx` module we do have. The fallback code
# path in starlette uses the identical `httpx` API either way, so this only
# changes which import branch runs, not behavior.
try:
    import httpx2  # noqa: F401
except ModuleNotFoundError:
    import httpx

    sys.modules["httpx2"] = httpx

from bootleg.config import Library


@pytest.fixture
def library(tmp_path) -> Library:
    for sub in ("_inbox", "sessions", "reels"):
        (tmp_path / sub).mkdir()
    return Library.open(tmp_path)
