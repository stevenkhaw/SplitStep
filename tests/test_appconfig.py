from pathlib import Path

import pytest

from splitstep import appconfig
from splitstep.appconfig import LibraryUnconfigured, resolve_library, save_config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point the config file into tmp and clear the env var, so these tests
    can never read or write the developer's real configuration."""
    monkeypatch.setattr(appconfig, "config_path", lambda: tmp_path / "config.json")
    monkeypatch.delenv(appconfig.ENV_VAR, raising=False)
    return tmp_path


def test_flag_wins_over_everything(isolated_config, monkeypatch):
    monkeypatch.setenv(appconfig.ENV_VAR, "/env/lib")
    save_config({"library": "/config/lib"})
    assert resolve_library("/flag/lib") == Path("/flag/lib")


def test_env_wins_over_config_file(isolated_config, monkeypatch):
    monkeypatch.setenv(appconfig.ENV_VAR, "/env/lib")
    save_config({"library": "/config/lib"})
    assert resolve_library(None) == Path("/env/lib")


def test_config_file_is_the_last_fallback(isolated_config):
    save_config({"library": "/config/lib"})
    assert resolve_library(None) == Path("/config/lib")


def test_nothing_configured_raises_with_all_three_mechanisms_named(isolated_config):
    with pytest.raises(LibraryUnconfigured) as exc:
        resolve_library(None)
    msg = str(exc.value)
    assert "--library" in msg
    assert appconfig.ENV_VAR in msg
    assert "config" in msg


def test_save_and_load_round_trip(isolated_config):
    save_config({"library": "/a", "mode": "friend"})
    assert appconfig.load_config() == {"library": "/a", "mode": "friend"}


def test_missing_config_file_loads_empty(isolated_config):
    assert appconfig.load_config() == {}


def test_corrupt_config_file_raises_library_unconfigured_naming_the_path(isolated_config):
    # A corrupt file gets the same friendly path as a missing library: both
    # mean "no library configured", and this lets main()'s existing
    # LibraryUnconfigured handler turn it into exit 2 with a message instead
    # of a raw json.JSONDecodeError traceback.
    appconfig.config_path().write_text("{not valid json")
    with pytest.raises(LibraryUnconfigured) as exc:
        resolve_library(None)
    msg = str(exc.value)
    assert str(appconfig.config_path()) in msg


def test_tilde_expands(isolated_config):
    assert resolve_library("~/lib") == Path("~/lib").expanduser()


def test_mode_defaults_to_dev(isolated_config):
    assert appconfig.get_mode() == "dev"


def test_unknown_mode_in_the_file_reads_as_dev(isolated_config):
    # A hand-edited config with a typo'd mode must not strand the UI in an
    # undefined state; the resolver collapses anything unrecognized to 'dev'.
    save_config({"mode": "expert"})
    assert appconfig.get_mode() == "dev"


def test_set_mode_preserves_other_keys(isolated_config):
    save_config({"library": "/some/path"})
    appconfig.set_mode("friend")
    assert appconfig.get_mode() == "friend"
    assert appconfig.load_config()["library"] == "/some/path"


def test_set_mode_rejects_unknown_values(isolated_config):
    with pytest.raises(ValueError):
        appconfig.set_mode("expert")


def test_corrupt_config_reads_as_dev_but_refuses_writes(isolated_config):
    # Read path: /api/config runs on every app boot and must never fail over
    # a config problem the serve process itself does not have. Write path:
    # overwriting an unparseable file could discard a hand-edited library
    # path, so set_mode propagates the friendly error instead.
    appconfig.config_path().write_text("{not valid json")
    assert appconfig.get_mode() == "dev"
    with pytest.raises(LibraryUnconfigured):
        appconfig.set_mode("friend")


def test_save_config_leaves_no_temp_file_behind(isolated_config):
    save_config({"mode": "dev"})
    names = [p.name for p in appconfig.config_path().parent.iterdir()]
    assert names == ["config.json"]
