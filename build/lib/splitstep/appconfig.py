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
    # Temp-file + os.replace, not a direct write_text: write_text truncates
    # before it writes, so a concurrent reader (another request thread's
    # get_mode, another process's resolve_library) could catch the file
    # half-written and misread "no library configured". The replace is atomic
    # within the filesystem, so readers see the old file or the new one,
    # never a torn middle.
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(cfg, indent=1) + "\n")
    os.replace(tmp, path)


MODES = ("friend", "dev")


def get_mode() -> str:
    # 'dev' when the key is absent: a dev checkout never wrote it, and the
    # Tauri shell (Phase 3) writes 'friend' on first run -- so absence itself
    # is the dev signal, no second flag needed. Unknown values also collapse
    # to 'dev': a hand-edited typo must not strand the UI in a mode no gate
    # was written for. A file too corrupt to parse gets the same treatment
    # on this read path -- LibraryUnconfigured is the CLI's friendly exit,
    # but /api/config calls this on every app boot and must never 500 over
    # a config problem the serve process itself (started via --library)
    # does not have.
    try:
        mode = load_config().get("mode", "dev")
    except LibraryUnconfigured:
        return "dev"
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


def known_libraries() -> list[str]:
    """Libraries opened before, most recent first.

    Tolerant of a corrupt or absent key for the same reason get_mode() is:
    the front layer is the one screen that can repair a bad config, so it has
    to be able to render against one. A non-list value collapses to empty
    rather than raising -- a hand-edited typo should cost the list, not the
    app.
    """
    try:
        value = load_config().get("libraries", [])
    except LibraryUnconfigured:
        return []
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str)]


def remember_library(path: str | Path) -> None:
    """Record a library as opened, newest first, deduped.

    Paths are stored expanded and absolute: the list is shown to a human
    picking between drives, and `~/Movies/SplitStep` sitting beside
    `/Users/x/Movies/SplitStep` would read as two libraries when it is one.

    Unreachable entries are deliberately NOT pruned. An unplugged drive is
    the single most common reason to be looking at this list, and forgetting
    it is the one thing the list must not do -- reachability is decided at
    render time instead.
    """
    resolved = str(Path(path).expanduser())
    try:
        cfg = load_config()
    except LibraryUnconfigured:
        # `config set-library` is a plausible thing to run *because* the
        # config is broken, so a corrupt file is replaced rather than fatal.
        cfg = {}
    existing = [entry for entry in known_libraries() if entry != resolved]
    cfg["libraries"] = [resolved, *existing]
    save_config(cfg)
