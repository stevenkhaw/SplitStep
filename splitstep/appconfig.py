"""Where the library path comes from when no --library flag is given.

Resolution order: flag > SPLITSTEP_LIBRARY env var > config file. The flag
stays first so a one-off command against a second library never needs the
configuration touched; the config file is last so it is the thing a
double-clicked app can write once and forget. The config file also carries
`mode` (friend/dev, read in Phase 2) -- one JSON file, one writer at a time.
"""

import json
import os
from pathlib import Path

from platformdirs import user_config_dir

ENV_VAR = "SPLITSTEP_LIBRARY"


class LibraryUnconfigured(Exception):
    """No library path from any of the three mechanisms."""


def config_path() -> Path:
    # user_config_dir handles the per-OS convention (~/Library/Application
    # Support on macOS) so the Tauri shell and the CLI agree on the location
    # without either hardcoding a platform.
    return Path(user_config_dir("splitstep")) / "config.json"


def load_config() -> dict:
    try:
        return json.loads(config_path().read_text())
    except FileNotFoundError:
        return {}


def save_config(cfg: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=1) + "\n")


def resolve_library(flag: str | None) -> Path:
    if flag:
        return Path(flag).expanduser()
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser()
    configured = load_config().get("library")
    if configured:
        return Path(configured).expanduser()
    raise LibraryUnconfigured(
        "No library configured. Pass --library <path>, set the "
        f"{ENV_VAR} environment variable, or run "
        "`splitstep config set-library <path>` once "
        f"(config file: {config_path()})."
    )
