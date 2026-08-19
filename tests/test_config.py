import pytest

from bootleg.config import Library, LibraryNotMounted, NotEnoughSpace


def test_open_returns_library_for_existing_writable_root(tmp_path):
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


def test_source_dir_is_zero_padded(tmp_path):
    lib = Library.open(tmp_path)
    sources = tmp_path / "sessions" / "2026-08-19" / "sources"
    assert lib.source_dir("2026-08-19", 1) == sources / "01"
    assert lib.source_dir("2026-08-19", 12) == sources / "12"


def test_free_bytes_is_positive(tmp_path):
    assert Library.open(tmp_path).free_bytes() > 0


def test_require_free_passes_for_a_small_request(tmp_path):
    Library.open(tmp_path).require_free(1024)  # must not raise


def test_require_free_raises_before_a_write_that_cannot_fit(tmp_path):
    lib = Library.open(tmp_path)
    with pytest.raises(NotEnoughSpace) as exc:
        lib.require_free(lib.free_bytes() + 10**12)
    assert "space" in str(exc.value).lower()
