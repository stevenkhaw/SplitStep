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
    except json.JSONDecodeError as exc:
        # LibraryUnconfigured, not a bare re-raise: a config file that cannot
        # be parsed carries no library path, which is exactly the condition
        # that exception already names, and every caller (main()'s except
        # clauses) already turns it into a friendly stderr message and exit 2
        # instead of a raw traceback. A corrupt file is functionally identical
        # to a missing one from the resolver's point of view -- both mean
        # "no library configured" -- so it gets the same friendly path, with
        # the file's path in the message so the fix (edit or delete it) is
        # obvious.
        raise LibraryUnconfigured(
            f"Config file at {config_path()} is not valid JSON ({exc}). "
            "Fix it or delete the file and re-run `splitstep config set-library <path>`."
        ) from exc


def save_config(cfg: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=1) + "\n")


MODES = ("friend", "dev")


def get_mode() -> str:
    # 'dev' when the key is absent: a dev checkout never wrote it, and the
    # Tauri shell (Phase 3) writes 'friend' on first run -- so absence itself
    # is the dev signal, no second flag needed. Unknown values also collapse
    # to 'dev': a hand-edited typo must not strand the UI in a mode no gate
    # was written for.
    mode = load_config().get("mode", "dev")
    return mode if mode in MODES else "dev"


def set_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    cfg = load_config()
    cfg["mode"] = mode
    save_config(cfg)


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
