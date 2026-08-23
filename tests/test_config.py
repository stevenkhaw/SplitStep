import pytest

from splitstep.config import (
    Library,
    LibraryAlreadyInitialized,
    LibraryNotMounted,
    NotEnoughSpace,
)
from splitstep.db.schema import connect, migrate


def _init_db(root) -> None:
    """Create just the database, for tests exercising something other than
    Library.open()'s own guard."""
    conn = connect(root / "library.db")
    migrate(conn)
    conn.close()


def test_open_returns_library_for_existing_writable_root(tmp_path):
    _init_db(tmp_path)
    lib = Library.open(tmp_path)
    assert lib.root == tmp_path
    assert lib.db_path == tmp_path / "library.db"
    assert lib.inbox == tmp_path / "_inbox"
    assert lib.sessions_dir == tmp_path / "sessions"
    assert lib.reels_dir == tmp_path / "reels"


def test_open_raises_when_root_missing(tmp_path):
    missing = tmp_path / "not-plugged-in"
    with pytest.raises(LibraryNotMounted) as exc:
        Library.open(missing)
    assert str(missing) in str(exc.value)


def test_open_does_not_create_the_root(tmp_path):
    missing = tmp_path / "not-plugged-in"
    with pytest.raises(LibraryNotMounted):
        Library.open(missing)
    assert not missing.exists()


def test_open_raises_when_library_db_is_missing(tmp_path):
    """The leftover-mountpoint scenario: a directory exists, is writable,
    and even has the full _inbox/sessions/reels tree (e.g. a stale, empty
    `/Volumes/SplitStep` mountpoint after an unclean eject looks exactly
    like this from the filesystem's point of view) -- but was never actually
    initialized. Before this guard, Library.open() only checked is_dir() and
    W_OK, and the first sqlite3.connect() anywhere downstream silently
    created library.db right here, starting a second, empty library on
    internal storage instead of refusing to start.
    """
    for sub in ("_inbox", "sessions", "reels"):
        (tmp_path / sub).mkdir()
    with pytest.raises(LibraryNotMounted) as exc:
        Library.open(tmp_path)
    assert str(tmp_path) in str(exc.value)
    assert not (tmp_path / "library.db").exists()


def test_source_dir_is_zero_padded(tmp_path):
    _init_db(tmp_path)
    lib = Library.open(tmp_path)
    sources = tmp_path / "sessions" / "2026-08-19" / "sources"
    assert lib.source_dir("2026-08-19", 1) == sources / "01"
    assert lib.source_dir("2026-08-19", 12) == sources / "12"


def test_free_bytes_is_positive(tmp_path):
    _init_db(tmp_path)
    assert Library.open(tmp_path).free_bytes() > 0


def test_require_free_passes_for_a_small_request(tmp_path):
    _init_db(tmp_path)
    Library.open(tmp_path).require_free(1024)  # must not raise


def test_require_free_raises_before_a_write_that_cannot_fit(tmp_path):
    _init_db(tmp_path)
    lib = Library.open(tmp_path)
    with pytest.raises(NotEnoughSpace) as exc:
        lib.require_free(lib.free_bytes() + 10**12)
    assert "space" in str(exc.value).lower()


# -- Library.create() / `splitstep init` --------------------------------------

def test_create_builds_the_tree_and_an_openable_database(tmp_path):
    lib = Library.create(tmp_path)
    assert lib.inbox.is_dir()
    assert lib.sessions_dir.is_dir()
    assert lib.reels_dir.is_dir()
    assert lib.db_path.exists()

    # And Library.open() now accepts it -- the whole point.
    reopened = Library.open(tmp_path)
    assert reopened.root == tmp_path


def test_create_refuses_when_a_library_already_exists(tmp_path):
    Library.create(tmp_path)
    with pytest.raises(LibraryAlreadyInitialized) as exc:
        Library.create(tmp_path)
    assert str(tmp_path) in str(exc.value)


def test_create_raises_when_the_root_is_not_mounted(tmp_path):
    missing = tmp_path / "not-plugged-in"
    with pytest.raises(LibraryNotMounted):
        Library.create(missing)
    assert not missing.exists()
