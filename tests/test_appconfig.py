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


def test_tilde_expands(isolated_config):
    assert resolve_library("~/lib") == Path("~/lib").expanduser()
