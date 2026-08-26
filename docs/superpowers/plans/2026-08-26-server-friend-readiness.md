# Server Friend-Readiness Implementation Plan (Phase 1 of the Mac app spec)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Python server installable and operable without a terminal-shaped hole — optional `--library` with env/config fallback, bundle-aware resource paths, in-app detect/retry/import routes, honest inbox and job errors, the enqueue-race fix, and a per-library clip colour profile.

**Architecture:** Pure Python changes to `splitstep/` plus two SQL migrations. No UI work (Phase 2), no Tauri (Phase 3). Every change also improves the dev checkout and a plain `pip install`. Spec: `docs/superpowers/specs/2026-08-26-mac-app-distribution-design.md`.

**Tech Stack:** Python 3.12, FastAPI (sync `def` routes), sqlite, pytest. New deps: `platformdirs`, `python-multipart`.

## Global Constraints

- Python interpreter: `~/miniconda3/envs/splitstep/bin/python` (conda env `splitstep`). Run tests as `~/miniconda3/envs/splitstep/bin/python -m pytest …` — the `-m` form is REQUIRED in a git worktree, or you silently import master's code.
- `pytest` runs with `filterwarnings = ["error"]` — a new warning fails the suite.
- ruff line-length 100: `~/miniconda3/envs/splitstep/bin/ruff check splitstep tests` must pass before every commit.
- Comments explain **why**, not what. Match the existing rationale-comment density; never strip existing rationale comments.
- Migrations are numbered `.sql` files in `splitstep/db/migrations/`, applied by `PRAGMA user_version`. Add files `010_…` and `011_…`; never edit an applied one. Current highest: `009_rally_split.sql`.
- Every API route is `def`, not `async def` (thread-per-request with `ThreadLocalConnections`).
- Commit messages follow repo style: lowercase `type(scope): imperative summary`, rationale in body when the why is non-obvious.
- Baseline before starting: full suite green (`~/miniconda3/envs/splitstep/bin/python -m pytest -q`).

**Parallel human track (not a task):** Gate 0 — the fence-mounted clip in `_inbox` runs the full pipeline on Steven's machine; verdict recorded in `docs/superpowers/plans/`. Independent of every task below.

---

### Task 1: Config resolution module (`appconfig.py`)

**Files:**
- Create: `splitstep/appconfig.py`
- Test: `tests/test_appconfig.py`
- Modify: `pyproject.toml:5-13` (add dependency)

**Interfaces:**
- Produces: `resolve_library(flag: str | None) -> Path` (raises `LibraryUnconfigured`), `load_config() -> dict`, `save_config(cfg: dict) -> None`, `config_path() -> Path`, `ENV_VAR = "SPLITSTEP_LIBRARY"`. Task 2 (CLI) and Phase 3 (Tauri writes the same JSON file) consume these.

- [ ] **Step 1: Install the new dependency and declare it**

```bash
~/miniconda3/envs/splitstep/bin/pip install 'platformdirs>=4.0'
```

In `pyproject.toml`, append to the `dependencies` list (after `"watchdog>=5.0",`):

```toml
    "platformdirs>=4.0",
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_appconfig.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_appconfig.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'splitstep.appconfig'`

- [ ] **Step 4: Implement `splitstep/appconfig.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass, plus ruff**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_appconfig.py -q`
Expected: 7 passed
Run: `~/miniconda3/envs/splitstep/bin/ruff check splitstep tests`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add splitstep/appconfig.py tests/test_appconfig.py pyproject.toml
git commit -m "feat: resolve the library from flag, env, or config file"
```

---

### Task 2: CLI wiring — optional `--library`, `config` commands, `serve --create`

**Files:**
- Modify: `splitstep/cli.py` (lines 31-32 `_library`, 76-84 `cmd_init`, 159-177 `cmd_serve`, 487-596 `main`/parser)
- Modify: `splitstep/config.py` (add `Library.open_or_create`)
- Test: `tests/test_cli.py` (append), `tests/test_config.py` (append)

**Interfaces:**
- Consumes: `appconfig.resolve_library`, `appconfig.save_config`, `appconfig.load_config`, `appconfig.LibraryUnconfigured` (Task 1).
- Produces: `Library.open_or_create(root: Path) -> Library`; CLI surface `splitstep config show`, `splitstep config set-library <path>`, `splitstep serve --create`. Phase 3's Tauri shell invokes `serve --library <picked> --create`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_open_or_create_initializes_an_empty_directory(tmp_path):
    lib = Library.open_or_create(tmp_path)
    assert (tmp_path / "library.db").exists()
    assert (tmp_path / "_inbox").is_dir()
    assert lib.root == tmp_path


def test_open_or_create_opens_an_existing_library_untouched(tmp_path):
    Library.create(tmp_path)
    before = (tmp_path / "library.db").stat().st_mtime_ns
    lib = Library.open_or_create(tmp_path)
    assert lib.root == tmp_path
    assert (tmp_path / "library.db").stat().st_mtime_ns == before


def test_open_or_create_still_refuses_a_missing_root(tmp_path):
    with pytest.raises(LibraryNotMounted):
        Library.open_or_create(tmp_path / "nope")
```

(`tests/test_config.py` already imports `Library` and `LibraryNotMounted`; add either import if missing at the top.)

Append to `tests/test_cli.py`:

```python
from splitstep import appconfig
from splitstep.cli import main


@pytest.fixture(autouse=True)
def isolated_appconfig(tmp_path, monkeypatch):
    # Same isolation as tests/test_appconfig.py: never touch the real config.
    monkeypatch.setattr(appconfig, "config_path", lambda: tmp_path / "appconfig.json")
    monkeypatch.delenv(appconfig.ENV_VAR, raising=False)


def test_library_flag_is_now_optional_and_env_var_works(library, monkeypatch, capsys):
    monkeypatch.setenv(appconfig.ENV_VAR, str(library.root))
    assert main(["doctor"]) == 0
    assert str(library.root) in capsys.readouterr().out


def test_no_library_anywhere_exits_2_with_guidance(capsys):
    assert main(["doctor"]) == 2
    err = capsys.readouterr().err
    assert "--library" in err and appconfig.ENV_VAR in err


def test_config_set_library_then_commands_need_no_flag(library, capsys):
    assert main(["config", "set-library", str(library.root)]) == 0
    assert main(["doctor"]) == 0


def test_config_set_library_refuses_a_missing_directory(tmp_path, capsys):
    assert main(["config", "set-library", str(tmp_path / "nope")]) == 1


def test_config_show_reports_unset(capsys):
    assert main(["config", "show"]) == 0
    assert "unset" in capsys.readouterr().out
```

(If `tests/test_cli.py` does not already import `pytest`, add it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_cli.py tests/test_config.py -q`
Expected: the new tests FAIL (`open_or_create` missing; `main(["doctor"])` exits with argparse error because `--library` is required)

- [ ] **Step 3: Implement**

In `splitstep/config.py`, add below `create` (keep both existing methods byte-identical — `open`'s empty-mountpoint guard is load-bearing):

```python
    @classmethod
    def open_or_create(cls, root: Path) -> "Library":
        """Open the library at `root`, initializing it first if the directory
        exists but holds no library.db.

        This is the first-run path for the app's folder picker: the user just
        chose the directory, so "no library.db here" means "make me one", not
        "the drive fell off". `open()`'s refusal stays untouched for every
        other caller -- an unattended `serve` after an unclean eject must
        still fail loudly rather than build a second library on internal
        storage. The directory itself must already exist; nothing here ever
        mkdirs a root (see InboxWatcher.start for the hazard).
        """
        root = Path(root)
        if (root / "library.db").exists():
            return cls.open(root)
        return cls.create(root)
```

In `splitstep/cli.py`:

1. Add to the imports: `from splitstep import appconfig` and extend the config import line to
   `from splitstep.config import Library, LibraryAlreadyInitialized, LibraryNotMounted`.
2. Replace `_library` (lines 31-32):

```python
def _library(args) -> Library:
    return Library.open(appconfig.resolve_library(args.library))
```

3. In `cmd_init` (line 79), replace `Path(args.library).expanduser()` with
   `appconfig.resolve_library(args.library)`.
4. In `cmd_serve`, replace `lib = _library(args)` (line 164) with:

```python
    root = appconfig.resolve_library(args.library)
    # --create is the app's first-run path: the folder was just picked by a
    # human, so initializing it is the intent. Everything else keeps open()'s
    # strict guard.
    lib = Library.open_or_create(root) if args.create else Library.open(root)
```

5. Add the two config commands (place near `cmd_doctor`):

```python
def cmd_config_show(args) -> int:
    cfg = appconfig.load_config()
    print(f"config file: {appconfig.config_path()}")
    print(f"library:     {cfg.get('library') or 'unset'}")
    env = os.environ.get(appconfig.ENV_VAR)
    if env:
        print(f"{appconfig.ENV_VAR} (overrides the file): {env}")
    return 0


def cmd_config_set_library(args) -> int:
    path = Path(args.path).expanduser()
    if not path.is_dir():
        # Refuse rather than mkdir: choosing where a library lives is the
        # picker's/user's job, and a typo here must not create a stray tree.
        print(f"not a directory: {path}", file=sys.stderr)
        return 1
    cfg = appconfig.load_config()
    cfg["library"] = str(path)
    appconfig.save_config(cfg)
    print(f"library set to {path}")
    return 0
```

Add `import os` to the module imports (it is not imported today).

6. In `main()`:
   - Change line 491 to `parser.add_argument("--library", help="path to the library root")` (drop `required=True`).
   - Add `--create` to the serve parser (after line 502):

```python
    p.add_argument("--create", action="store_true",
                   help="initialize the library first if the directory is empty")
```

   - Register the `config` group (near the other groups):

```python
    p = sub.add_parser("config", help="show or set persistent configuration")
    config_sub = p.add_subparsers(dest="config_command", required=True)

    cs = config_sub.add_parser("show", help="print the config file and resolved values")
    cs.set_defaults(func=cmd_config_show)

    cl = config_sub.add_parser("set-library", help="remember a default library path")
    cl.add_argument("path")
    cl.set_defaults(func=cmd_config_set_library)
```

   - Extend the exception handling at the bottom (lines 584-591) with one more clause:

```python
    except appconfig.LibraryUnconfigured as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
```

- [ ] **Step 4: Run the affected suites**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_cli.py tests/test_config.py -q`
Expected: PASS. Then the full suite (`… -m pytest -q`) — existing CLI tests pass `--library` explicitly, which still wins the resolution order, so nothing else should move. Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add splitstep/cli.py splitstep/config.py tests/test_cli.py tests/test_config.py
git commit -m "feat(cli): optional --library, config commands, serve --create"
```

---

### Task 3: Bundle-aware resource paths (`resources.py`) + loud SPA fallback

**Files:**
- Create: `splitstep/resources.py`
- Modify: `splitstep/accel.py:15-28`, `splitstep/media/transcode.py:48,135`, `splitstep/media/probe.py:178-187`, `splitstep/detect/vision.py:157,163`, `splitstep/detect/audio.py:23`, `splitstep/api/spa.py`, `splitstep/cli.py:165`
- Test: `tests/test_resources.py`

**Interfaces:**
- Produces: `ffmpeg_exe() -> str`, `ffprobe_exe() -> str` (both raise `RuntimeError` with a platform-conditional install hint when nothing is found), `yolo_weights() -> str`, `spa_dist() -> Path`, `bundle_dir() -> Path | None`. Phase 3's PyInstaller bundle places `ffmpeg`, `ffprobe`, `yolo11n.pt`, and `web_dist/` at the bundle root; these functions are the only place that layout is known.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resources.py`:

```python
import sys
from pathlib import Path

import pytest

from splitstep import resources


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    """Simulate a PyInstaller bundle: sys._MEIPASS points at tmp."""
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    return tmp_path


def test_bundled_ffmpeg_wins_over_path(frozen):
    (frozen / "ffmpeg").write_bytes(b"")
    assert resources.ffmpeg_exe() == str(frozen / "ffmpeg")


def test_unfrozen_falls_back_to_path_lookup():
    # The dev machine has a real ffmpeg; the resolved value must be absolute.
    assert Path(resources.ffmpeg_exe()).is_absolute()


def test_missing_ffmpeg_names_a_platform_install_hint(monkeypatch):
    monkeypatch.setattr(resources.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError) as exc:
        resources.ffmpeg_exe()
    assert "ffmpeg" in str(exc.value)


def test_bundled_weights_win(frozen):
    (frozen / "yolo11n.pt").write_bytes(b"")
    assert resources.yolo_weights() == str(frozen / "yolo11n.pt")


def test_unfrozen_weights_keep_the_ultralytics_default():
    assert resources.yolo_weights() == "yolo11n.pt"


def test_bundled_spa_wins(frozen):
    (frozen / "web_dist").mkdir()
    (frozen / "web_dist" / "index.html").write_text("x")
    assert resources.spa_dist() == frozen / "web_dist"


def test_unfrozen_spa_is_the_source_tree():
    assert resources.spa_dist().parts[-2:] == ("web", "dist")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_resources.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `splitstep/resources.py`**

```python
"""Where bundled binaries and assets live, bundle-first with dev fallbacks.

Under PyInstaller, `sys._MEIPASS` is the unpacked bundle root and everything
we ship (ffmpeg, ffprobe, yolo11n.pt, web_dist/) sits directly inside it --
this module is the only place that layout is written down. Unfrozen, each
function falls back to exactly what the code did before it existed: PATH for
the ffmpeg pair, ultralytics' cwd auto-download for the weights, the source
tree for the SPA. That keeps the dev loop byte-identical while making a
frozen app self-contained.
"""

import platform
import shutil
import sys
from pathlib import Path


def bundle_dir() -> Path | None:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


def _bundled(name: str) -> Path | None:
    base = bundle_dir()
    if base is not None:
        candidate = base / name
        if candidate.exists():
            return candidate
    return None


def _install_hint() -> str:
    if platform.system() == "Darwin":
        return "Install it: brew install ffmpeg"
    return "Install ffmpeg with your package manager (e.g. sudo apt install ffmpeg)."


def _exe(name: str) -> str:
    bundled = _bundled(name)
    if bundled is not None:
        return str(bundled)
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"{name} not found on PATH. {_install_hint()}")
    return found


def ffmpeg_exe() -> str:
    return _exe("ffmpeg")


def ffprobe_exe() -> str:
    return _exe("ffprobe")


def yolo_weights() -> str:
    bundled = _bundled("yolo11n.pt")
    if bundled is not None:
        return str(bundled)
    # Unfrozen: ultralytics resolves a bare name by downloading into cwd on
    # first use, which is fine for the dev checkout and wrong for an app
    # whose cwd may be / or a read-only translocated path -- hence the
    # bundle-first branch above.
    return "yolo11n.pt"


def spa_dist() -> Path:
    bundled = bundle_dir()
    if bundled is not None and (bundled / "web_dist" / "index.html").is_file():
        return bundled / "web_dist"
    return Path(__file__).parent.parent / "web" / "dist"
```

- [ ] **Step 4: Rewire the call sites**

Every literal `"ffmpeg"`/`"ffprobe"` subprocess vector goes through the resolver:

1. `splitstep/accel.py`: add `from splitstep.resources import ffmpeg_exe`. In `_ffmpeg_encoders` (line 18) replace `["ffmpeg", …]` with `[ffmpeg_exe(), …]`. In `detect_accel` replace lines 27-28 with:

```python
    ffmpeg_exe()  # raises with a platform-appropriate install hint if absent
```

   (Delete the now-unused `import shutil` if nothing else in the file uses it.)
2. `splitstep/media/transcode.py`: add the same import; line 48 `cmd = [ffmpeg_exe(), "-v", …]`; line 135 `[ffmpeg_exe(), "-v", "error", "-y", *args]`.
3. `splitstep/media/probe.py`: add `from splitstep.resources import ffprobe_exe`. In `ffprobe_json`, before the `subprocess.run` call add:

```python
    try:
        exe = ffprobe_exe()
    except RuntimeError as exc:
        raise ProbeError(str(exc)) from exc
```

   and use `[exe, "-v", "error", …]` in the vector. The existing `FileNotFoundError` clause (lines 184-187) stays as a race guard but its message becomes `str(exc)`-free: replace the hardcoded brew string with `f"ffprobe vanished while running: {exc}"`.
4. `splitstep/detect/vision.py`: line 157 `cmd = [ffmpeg_exe(), "-v", "error"]`; line 163 `model = YOLO(resources.yolo_weights())` — import `from splitstep import resources` and `from splitstep.resources import ffmpeg_exe`, and delete the `model_name` parameter from `iter_person_boxes` **only if nothing else passes it** (grep first: `grep -rn "model_name" splitstep tests` — if tests pass one, keep the parameter defaulting to `resources.yolo_weights()` instead).
5. `splitstep/detect/audio.py`: line 23 `[ffmpeg_exe(), "-v", "error", …]` with the import.
6. `splitstep/api/spa.py`: replace the whole body of `mount_spa` so a missing UI is a visible page, not a blank screen:

```python
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

_NO_UI_PAGE = """<!doctype html><meta charset="utf-8"><title>SplitStep</title>
<body style="font-family: system-ui; margin: 4rem auto; max-width: 32rem">
<h1>SplitStep is running, but the UI is not built</h1>
<p>The API is up. To get the interface, run <code>npm run build</code> in
<code>web/</code> and restart <code>splitstep serve</code>.</p></body>"""


def mount_spa(app: FastAPI, dist: Path) -> None:
    """Serve the built Svelte bundle at the root.

    Mounted last so /api and /media keep priority. A missing dist used to be
    a log-line warning and a blank browser page -- under a non-editable
    install that combination read as "the app is broken" with no clue where.
    Serving an explanation page keeps `splitstep serve` usable before the
    first `npm run build` while making the failure impossible to miss.
    """
    if not (dist / "index.html").is_file():
        log.error("no built UI at %s; serving instructions page. "
                  "Run `npm run build` in web/.", dist)

        @app.get("/", include_in_schema=False)
        def no_ui() -> HTMLResponse:
            return HTMLResponse(_NO_UI_PAGE, status_code=503)

        return
    app.mount("/", StaticFiles(directory=dist, html=True), name="spa")
```

7. `splitstep/cli.py:165`: replace the `spa_dist=Path(__file__)…` argument with `spa_dist=resources.spa_dist()` and add `from splitstep import resources` to the imports.

- [ ] **Step 5: Add a regression test for the fallback page**

Append to `tests/test_api.py`:

```python
def test_missing_spa_serves_an_explanation_not_a_blank_page(library, tmp_path):
    with TestClient(create_app(library, spa_dist=tmp_path / "nowhere")) as c:
        r = c.get("/")
    assert r.status_code == 503
    assert "npm run build" in r.text
```

- [ ] **Step 6: Run the full suite**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest -q`
Expected: PASS (the resolver returns the same PATH ffmpeg the literals found). Ruff clean.

- [ ] **Step 7: Commit**

```bash
git add splitstep/resources.py splitstep/accel.py splitstep/media/transcode.py \
  splitstep/media/probe.py splitstep/detect/vision.py splitstep/detect/audio.py \
  splitstep/api/spa.py splitstep/cli.py tests/test_resources.py tests/test_api.py
git commit -m "feat: resolve ffmpeg, weights and the SPA bundle-first"
```

---

### Task 4: Close the detect enqueue race (`enqueue_once`)

**Files:**
- Modify: `splitstep/db/jobs.py` (add `enqueue_once`), `splitstep/jobs/handlers.py:245-259`
- Test: `tests/test_jobs.py` (append)

**Interfaces:**
- Produces: `jobq.enqueue_once(conn, job_type: str, source_id: str, payload: dict) -> str | None` (None when an equivalent job is already queued/running). Task 5's detect route consumes it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_jobs.py` (it already imports `splitstep.db.jobs as jobq` — check the import name at the top of the file and match it):

```python
def test_enqueue_once_inserts_when_nothing_is_pending(conn):
    job_id = jobq.enqueue_once(conn, "detect", "src-1", {"source_id": "src-1"})
    assert job_id is not None
    assert jobq.has_pending_job(conn, "detect", "src-1")


def test_enqueue_once_returns_none_when_a_job_is_already_pending(conn):
    first = jobq.enqueue_once(conn, "detect", "src-1", {"source_id": "src-1"})
    second = jobq.enqueue_once(conn, "detect", "src-1", {"source_id": "src-1"})
    assert first is not None and second is None
    rows = conn.execute("SELECT COUNT(*) c FROM jobs WHERE type='detect'").fetchone()
    assert rows["c"] == 1


def test_enqueue_once_does_not_match_a_prefix_source_id(conn):
    jobq.enqueue_once(conn, "detect", "src-1", {"source_id": "src-1"})
    assert jobq.enqueue_once(conn, "detect", "src-10", {"source_id": "src-10"}) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_jobs.py -q`
Expected: FAIL with `AttributeError: … has no attribute 'enqueue_once'`

- [ ] **Step 3: Implement**

In `splitstep/db/jobs.py`, add after `enqueue_reel_once` (mirroring its shape — same lock, same reason):

```python
def enqueue_once(
    conn: sqlite3.Connection, job_type: str, source_id: str, payload: dict
) -> str | None:
    """Enqueue unless a `job_type` job for `source_id` is already pending.

    The check-then-act version of this (has_pending_job, then enqueue) was
    the one TODO in the repo: with two serve processes -- exactly what the
    Mac app's single-instance guard exists to prevent but must not rely on --
    both could pass the check and both insert, and a duplicate detect calls
    replace_rallies twice, silently discarding hand-edited boundaries.
    BEGIN IMMEDIATE takes the write lock before the SELECT for the same
    reason claim() and enqueue_reel_once do; the INSERT is inline rather
    than via enqueue() so check and insert commit exactly once, under the
    one lock.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT 1 FROM jobs WHERE type = ? AND status IN ('queued', 'running')"
            " AND json_extract(payload, '$.source_id') = ? LIMIT 1",
            (job_type, source_id),
        ).fetchone()
        if row is not None:
            conn.commit()
            return None
        job_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO jobs (id,type,payload,status,created_at)"
            " VALUES (?,?,?,'queued',?)",
            (job_id, job_type, json.dumps(payload), _now()),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return job_id
```

In `splitstep/jobs/handlers.py`, replace lines 245-259 (the comment block, the TODO, and the `if not jobq.has_pending_job…enqueue` pair) with:

```python
    # reclaim_stale() can requeue build_proxy if a worker dies after this
    # enqueue but before the job itself is written 'done', re-running this
    # handler; a duplicate detect job would call replace_rallies again and
    # silently discard any rally boundaries a human hand-edited between the
    # two detect runs (replace_rallies only preserves starred/rejected).
    # enqueue_once holds BEGIN IMMEDIATE across check and insert, so two
    # concurrent serve processes racing this window cannot both enqueue.
    jobq.enqueue_once(conn, "detect", source["id"], {"source_id": source["id"]})
```

- [ ] **Step 4: Run tests**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_jobs.py tests/test_handlers.py -q`
Expected: PASS. Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add splitstep/db/jobs.py splitstep/jobs/handlers.py tests/test_jobs.py
git commit -m "fix(jobs): close the check-then-act window on detect enqueue"
```

---

### Task 5: `POST /api/sources/{id}/detect` — re-detect without a terminal

**Files:**
- Modify: `splitstep/api/routes.py` (add route near `api_resegment`, line ~646)
- Test: `tests/test_api.py` (append)

**Interfaces:**
- Consumes: `jobq.enqueue_once` (Task 4).
- Produces: `POST /api/sources/{source_id}/detect` → `{"job_id": str | None, "already_running": bool}`; 404 unknown source, 409 while the source is `needs_setup`/`ingesting` (no proxy to detect against). Phase 2's QuadEditor replaces its "use the CLI" copy with a button on this route.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
def test_detect_route_queues_a_full_detect(client, conn, seeded):
    conn.execute("UPDATE sources SET status='ready' WHERE id=?", (seeded["source_id"],))
    conn.commit()
    r = client.post(f"/api/sources/{seeded['source_id']}/detect")
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] and body["already_running"] is False
    row = conn.execute("SELECT payload FROM jobs WHERE type='detect'").fetchone()
    # Full detect on purpose: the route exists for "I just assigned a play
    # region", and cached features are already quad-shaped.
    assert "reuse_features" not in row["payload"]


def test_detect_route_reports_an_already_running_job(client, conn, seeded):
    conn.execute("UPDATE sources SET status='ready' WHERE id=?", (seeded["source_id"],))
    conn.commit()
    client.post(f"/api/sources/{seeded['source_id']}/detect")
    r = client.post(f"/api/sources/{seeded['source_id']}/detect")
    assert r.status_code == 200
    assert r.json() == {"job_id": None, "already_running": True}


def test_detect_route_refuses_a_source_awaiting_setup(client, conn, seeded):
    conn.execute("UPDATE sources SET status='needs_setup' WHERE id=?",
                 (seeded["source_id"],))
    conn.commit()
    assert client.post(f"/api/sources/{seeded['source_id']}/detect").status_code == 409


def test_detect_route_404s_an_unknown_source(client, seeded):
    assert client.post("/api/sources/nope/detect").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_api.py -q -k detect_route`
Expected: FAIL with 404/405 responses

- [ ] **Step 3: Implement**

In `splitstep/api/routes.py`, add after `api_resegment`:

```python
@router.post("/api/sources/{source_id}/detect")
def api_detect(source_id: str, request: Request):
    """Queue a full re-detect for one source.

    Always a full run, never reuse_features: this route exists for "I just
    assigned a play region", and the quad is applied when features are built,
    so cached features are already shaped by the old quad (see CLAUDE.md on
    --reuse-features). Idempotent at the queue: enqueue_once means mashing
    the button cannot stack duplicate fifteen-minute jobs.
    """
    conn = _conn(request)
    source = get_source(conn, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if source["status"] in ("needs_setup", "ingesting"):
        raise HTTPException(
            status_code=409,
            detail="This source has no proxy yet -- finish setup first.",
        )
    job_id = jobq.enqueue_once(conn, "detect", source_id, {"source_id": source_id})
    return {"job_id": job_id, "already_running": job_id is None}
```

- [ ] **Step 4: Run tests**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_api.py -q`
Expected: PASS. Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add splitstep/api/routes.py tests/test_api.py
git commit -m "feat(api): re-detect a source without the CLI"
```

---

### Task 6: Split job errors into a sentence and a detail (migration 010)

**Files:**
- Create: `splitstep/db/migrations/010_job_error_detail.sql`
- Modify: `splitstep/db/jobs.py:163-168` (`finish`), `splitstep/jobs/worker.py:105-113`, `splitstep/api/routes.py:736-742` (`api_jobs`)
- Test: `tests/test_jobs.py`, `tests/test_api.py` (append)

**Interfaces:**
- Produces: `jobs.error` now holds a short human sentence; new column `jobs.error_detail` holds the traceback. `jobq.finish(conn, job_id, error=None, error_detail=None)`. `/api/jobs` rows gain `error_detail`. Phase 2 renders `error` in the badge and `error_detail` behind an expander.

- [ ] **Step 1: Write the migration**

Create `splitstep/db/migrations/010_job_error_detail.sql`:

```sql
-- The error column held a six-frame traceback, rendered raw into a 72px
-- badge panel. Split it: `error` becomes the one human sentence the UI
-- shows, `error_detail` keeps the traceback for whoever needs it. Existing
-- failed rows keep their traceback in `error` -- stale but harmless, and a
-- retry (also new in this phase) rewrites both columns.
ALTER TABLE jobs ADD COLUMN error_detail TEXT;
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_jobs.py`:

```python
def test_finish_stores_sentence_and_detail_separately(conn):
    job_id = jobq.enqueue(conn, "detect", {"source_id": "s"})
    jobq.finish(conn, job_id, error="short and human", error_detail="Traceback...")
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert row["error"] == "short and human"
    assert row["error_detail"] == "Traceback..."
```

Append to `tests/test_jobs.py` (worker level — the file already builds a `Worker`; follow its existing pattern for constructing one against the `library` fixture):

```python
def test_worker_failure_yields_a_sentence_not_a_traceback(library, conn):
    def explode(_library, _payload, _progress):
        raise ValueError("the proxy is missing")

    jobq.enqueue(conn, "boom", {"source_id": "s"})
    Worker(library, {"boom": explode}).run_once()
    row = conn.execute("SELECT * FROM jobs WHERE type='boom'").fetchone()
    assert row["error"] == "the proxy is missing"
    assert "Traceback" in row["error_detail"]
```

(Add `from splitstep.jobs.worker import Worker` to the test file's imports if absent.)

Append to `tests/test_api.py`:

```python
def test_jobs_route_carries_error_detail(client, conn, seeded):
    job_id = jobq.enqueue(conn, "detect", {"source_id": seeded["source_id"]})
    jobq.finish(conn, job_id, error="it broke", error_detail="Traceback...")
    jobs = client.get("/api/jobs").json()
    failed = next(j for j in jobs if j["id"] == job_id)
    assert failed["error"] == "it broke"
    assert failed["error_detail"] == "Traceback..."
```

(Add `from splitstep.db import jobs as jobq` to `tests/test_api.py` imports if absent.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_jobs.py tests/test_api.py -q`
Expected: FAIL (`finish()` rejects the keyword; no such column until the migration file lands — it is picked up automatically by `migrate()`)

- [ ] **Step 4: Implement**

`splitstep/db/jobs.py`, replace `finish`:

```python
def finish(
    conn: sqlite3.Connection,
    job_id: str,
    error: str | None = None,
    error_detail: str | None = None,
) -> None:
    conn.execute(
        "UPDATE jobs SET status=?, error=?, error_detail=?, finished_at=? WHERE id=?",
        ("failed" if error else "done", error, error_detail, _now(), job_id),
    )
    conn.commit()
```

`splitstep/jobs/worker.py`: add a module-level helper and change the `except` arm of `run_once`:

```python
def _error_summary(exc: BaseException) -> str:
    """One line a reviewer can act on, not a stack.

    Handlers already raise with good sentences (TranscodeError's colour
    message, ValueError's "No proxy on disk for source …"); str(exc) is
    that sentence. The class name is the fallback for exceptions raised
    bare, and the truncation guards against an exception whose repr is a
    payload dump.
    """
    text = str(exc).strip() or type(exc).__name__
    first_line = text.splitlines()[0]
    return first_line[:300]
```

```python
        except Exception as exc:
            jobq.finish(
                self.conn, job["id"],
                error=_error_summary(exc),
                error_detail=traceback.format_exc(limit=6),
            )
            log.exception("job %s failed", job["id"])
```

Also update the unknown-handler arm (line 95) to keep both columns honest:

```python
            jobq.finish(self.conn, job["id"], error=f"Unknown job type: {job['type']}")
```

(unchanged text, no detail — there is no traceback to keep).

`splitstep/api/routes.py` `api_jobs` (line 738-741): add the column to the SELECT:

```python
        "SELECT id,type,status,progress,error,error_detail,created_at,finished_at"
        " FROM jobs ORDER BY created_at DESC LIMIT 50"
```

- [ ] **Step 5: Run the full suite**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest -q`
Expected: PASS — any existing test asserting a traceback lands in `error` will surface here; update such assertions to read `error_detail` for the traceback and `error` for the sentence (grep: `grep -rn "Traceback" tests/`). Ruff clean.

- [ ] **Step 6: Commit**

```bash
git add splitstep/db/migrations/010_job_error_detail.sql splitstep/db/jobs.py \
  splitstep/jobs/worker.py splitstep/api/routes.py tests/test_jobs.py tests/test_api.py
git commit -m "feat(jobs): a failed job carries a sentence, the traceback moves aside"
```

---

### Task 7: `POST /api/jobs/{id}/retry`

**Files:**
- Modify: `splitstep/db/jobs.py` (add `retry`), `splitstep/api/routes.py` (route after `api_jobs`)
- Test: `tests/test_jobs.py`, `tests/test_api.py` (append)

**Interfaces:**
- Consumes: `error_detail` column (Task 6).
- Produces: `jobq.retry(conn, job_id) -> bool`; `POST /api/jobs/{job_id}/retry` → `{"ok": true}`, 404 unknown, 409 not-failed. Phase 2's failed-job panel consumes it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_jobs.py`:

```python
def test_retry_requeues_a_failed_job_and_clears_its_error(conn):
    job_id = jobq.enqueue(conn, "detect", {"source_id": "s"})
    jobq.finish(conn, job_id, error="boom", error_detail="Traceback...")
    assert jobq.retry(conn, job_id) is True
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "queued"
    assert row["error"] is None and row["error_detail"] is None
    assert row["finished_at"] is None and row["heartbeat_at"] is None


def test_retry_refuses_a_job_that_did_not_fail(conn):
    job_id = jobq.enqueue(conn, "detect", {"source_id": "s"})
    assert jobq.retry(conn, job_id) is False
```

Append to `tests/test_api.py`:

```python
def test_retry_route_requeues_only_failed_jobs(client, conn, seeded):
    job_id = jobq.enqueue(conn, "detect", {"source_id": seeded["source_id"]})
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 409
    jobq.finish(conn, job_id, error="boom")
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 200
    assert client.post("/api/jobs/nope/retry").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_jobs.py tests/test_api.py -q -k retry`
Expected: FAIL

- [ ] **Step 3: Implement**

`splitstep/db/jobs.py`:

```python
def retry(conn: sqlite3.Connection, job_id: str) -> bool:
    """Requeue one failed job in place. True if a row actually flipped.

    In place rather than a fresh row: the payload is the job, handlers are
    idempotent, and a new id would orphan whatever the UI is currently
    pointing at. Guarded on status='failed' in the WHERE so a double-click
    cannot requeue a job that is already running again.
    """
    cur = conn.execute(
        "UPDATE jobs SET status='queued', error=NULL, error_detail=NULL,"
        " finished_at=NULL, heartbeat_at=NULL, progress=NULL"
        " WHERE id=? AND status='failed'",
        (job_id,),
    )
    conn.commit()
    return cur.rowcount == 1
```

`splitstep/api/routes.py`, after `api_jobs`:

```python
@router.post("/api/jobs/{job_id}/retry")
def api_retry_job(job_id: str, request: Request):
    conn = _conn(request)
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not jobq.retry(conn, job_id):
        # Present but not failed -- done, queued, or running again already.
        raise HTTPException(status_code=409, detail="Only a failed job can be retried")
    return {"ok": True}
```

- [ ] **Step 4: Run tests**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_jobs.py tests/test_api.py -q`
Expected: PASS. Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add splitstep/db/jobs.py splitstep/api/routes.py tests/test_jobs.py tests/test_api.py
git commit -m "feat(jobs): retry a failed job from the API"
```

---

### Task 8: `POST /api/import` — upload into the inbox

**Files:**
- Modify: `splitstep/api/routes.py` (new route), `pyproject.toml` (add `python-multipart`)
- Test: `tests/test_api.py` (append)

**Interfaces:**
- Consumes: `VIDEO_SUFFIXES` from `splitstep/watcher.py`.
- Produces: `POST /api/import` (multipart field `file`) → `{"name": "<final name in _inbox>"}`; 415 non-video suffix. The existing watcher picks the file up on its next 5-second scan — the route writes, the watcher ingests, no new coupling. Phase 2's drop zone and the Tauri dock-drop both use it.

- [ ] **Step 1: Install and declare the multipart parser**

```bash
~/miniconda3/envs/splitstep/bin/pip install 'python-multipart>=0.0.9'
```

`pyproject.toml` dependencies list, append: `"python-multipart>=0.0.9",` (FastAPI's `UploadFile` needs it at request-parse time).

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_api.py`:

```python
def test_import_streams_into_the_inbox(client, library):
    r = client.post("/api/import",
                    files={"file": ("IMG_1234.MOV", b"fake video bytes")})
    assert r.status_code == 200
    name = r.json()["name"]
    assert (library.inbox / name).read_bytes() == b"fake video bytes"
    # No half-written temp left behind, and nothing dot-prefixed for the
    # watcher to trip on.
    assert [p.name for p in library.inbox.iterdir()] == [name]


def test_import_refuses_a_non_video_suffix(client, library):
    r = client.post("/api/import", files={"file": ("notes.txt", b"hi")})
    assert r.status_code == 415
    assert list(library.inbox.iterdir()) == []


def test_import_keeps_both_files_on_a_name_collision(client, library):
    client.post("/api/import", files={"file": ("a.mp4", b"one")})
    client.post("/api/import", files={"file": ("a.mp4", b"two")})
    names = sorted(p.name for p in library.inbox.iterdir())
    assert len(names) == 2 and names[0] == "a.mp4" and names[1].endswith(".mp4")


def test_import_strips_any_client_path_from_the_filename(client, library):
    r = client.post("/api/import", files={"file": ("../../evil.mp4", b"x")})
    assert r.status_code == 200
    assert r.json()["name"] == "evil.mp4"
    assert (library.inbox / "evil.mp4").exists()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_api.py -q -k import`
Expected: FAIL with 404s

- [ ] **Step 4: Implement**

In `splitstep/api/routes.py` — `os`, `uuid`, and `Path` are already imported at the top of the file; add only:

```python
from fastapi import UploadFile
from splitstep.watcher import VIDEO_SUFFIXES
```

Add the route (near `api_jobs`):

```python
@router.post("/api/import")
def api_import(request: Request, file: UploadFile):
    """Stream an upload into `_inbox/`, where the watcher takes over.

    The route writes, the watcher ingests -- importing this way and dropping
    a file in Finder are the same pipeline from the first probe onward.
    Loopback upload is fast enough for multi-GB originals, and it is the one
    mechanism that works identically in a plain browser and in the app.

    Written to a dot-prefixed temp name first: the watcher skips dotfiles,
    so it can never see a half-streamed upload (its is_stable check guards
    Finder copies, but an http stream that stalls for a while would pass a
    size-settle check while still incomplete). os.replace onto the final
    name is atomic within the filesystem, so the watcher sees either nothing
    or a complete file.
    """
    library = _library(request)
    name = Path(file.filename or "").name  # strip any client-supplied path
    if not name or Path(name).suffix.lower() not in VIDEO_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail="Not a video file. Supported: "
                   + ", ".join(sorted(VIDEO_SUFFIXES)),
        )
    dest = library.inbox / name
    if dest.exists():
        # Keep both: the same phone exports the same default names, and a
        # second session's IMG_0001 must not overwrite the first's.
        dest = library.inbox / f"{dest.stem}-{uuid.uuid4().hex[:8]}{dest.suffix}"
    tmp = library.inbox / f".upload-{uuid.uuid4().hex}{dest.suffix}"
    try:
        with tmp.open("wb") as out:
            while chunk := file.file.read(1024 * 1024):
                out.write(chunk)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return {"name": dest.name}
```

- [ ] **Step 5: Run tests**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_api.py -q`
Expected: PASS. Ruff clean.

- [ ] **Step 6: Commit**

```bash
git add splitstep/api/routes.py tests/test_api.py pyproject.toml
git commit -m "feat(api): upload into the inbox over loopback"
```

---

### Task 9: Inbox honesty — log skipped files once, surface them and the quarantine

**Files:**
- Modify: `splitstep/watcher.py:51-66` (`scan_inbox`), `:73-95` (`InboxWatcher`), `splitstep/api/routes.py` (new route)
- Test: `tests/test_watcher.py`, `tests/test_api.py` (append)

**Interfaces:**
- Produces: `GET /api/inbox` → `{"unsupported": [name…], "failed": [{"name": str, "error": str}…]}`. `scan_inbox` gains keyword `reported: set[str] | None = None`. Phase 2 shows both lists on the Library route.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_watcher.py` (check its existing imports — it exercises `scan_inbox(library, conn, settle_s=…)` already; match that call shape):

```python
def test_non_video_files_are_logged_once_not_every_scan(library, conn, caplog):
    (library.inbox / "match.webm").write_bytes(b"x")
    reported: set[str] = set()
    with caplog.at_level("WARNING"):
        scan_inbox(library, conn, settle_s=0.01, reported=reported)
        scan_inbox(library, conn, settle_s=0.01, reported=reported)
    mentions = [r for r in caplog.records if "match.webm" in r.getMessage()]
    assert len(mentions) == 1
```

Append to `tests/test_api.py`:

```python
def test_inbox_route_reports_unsupported_and_quarantined_files(client, library):
    (library.inbox / "match.webm").write_bytes(b"x")
    failed = library.inbox / "failed"
    failed.mkdir()
    (failed / "broken.mp4").write_bytes(b"x")
    (failed / "broken.mp4.error.txt").write_text("ProbeError: no video stream")
    body = client.get("/api/inbox").json()
    assert body["unsupported"] == ["match.webm"]
    assert body["failed"] == [
        {"name": "broken.mp4", "error": "ProbeError: no video stream"}
    ]


def test_inbox_route_is_empty_when_the_inbox_is(client, library):
    assert client.get("/api/inbox").json() == {"unsupported": [], "failed": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_watcher.py tests/test_api.py -q -k inbox`
Expected: FAIL (`scan_inbox` rejects the keyword; route 404s)

- [ ] **Step 3: Implement**

`splitstep/watcher.py` — change `scan_inbox`'s signature and the suffix branch:

```python
def scan_inbox(
    library: Library,
    conn: sqlite3.Connection,
    *,
    settle_s: float = 3.0,
    reported: set[str] | None = None,
) -> list[str]:
    enqueued: list[str] = []
    for path in sorted(library.inbox.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in VIDEO_SUFFIXES:
            # Once per file, not once per 5-second scan: the set is the
            # watcher's memory. Before this, a .webm dropped in the inbox
            # vanished silently, forever -- no log line, no UI signal.
            if reported is not None and path.name not in reported:
                reported.add(path.name)
                log.warning("ignoring non-video file in inbox: %s", path.name)
            continue
        if _already_queued(conn, path):
            continue
        if not is_stable(path, settle_s=settle_s, poll_s=min(0.5, settle_s / 2)):
            log.info("still copying, skipping this pass: %s", path.name)
            continue
        enqueued.append(jobq.enqueue(conn, "ingest", {"path": str(path)}))
    return enqueued
```

`InboxWatcher.__init__`: add `self._reported: set[str] = set()`. `_loop`: call `scan_inbox(self.library, self.conn, reported=self._reported)`.

`splitstep/api/routes.py`, add (near `api_import`):

```python
@router.get("/api/inbox")
def api_inbox(request: Request):
    """What the inbox is silently sitting on: files the watcher will never
    ingest (wrong suffix) and files that failed ingest (quarantined by
    _move_to_failed with a sibling .error.txt). Both were previously
    invisible to the UI -- "I dropped it and nothing happened" was the
    reported experience.
    """
    library = _library(request)
    unsupported = sorted(
        p.name for p in library.inbox.iterdir()
        if p.is_file() and not p.name.startswith(".")
        and p.suffix.lower() not in VIDEO_SUFFIXES
    )
    failed = []
    failed_dir = library.inbox / "failed"
    if failed_dir.is_dir():
        for err in sorted(failed_dir.glob("*.error.txt")):
            failed.append({
                "name": err.name.removesuffix(".error.txt"),
                "error": err.read_text().strip(),
            })
    return {"unsupported": unsupported, "failed": failed}
```

- [ ] **Step 4: Run tests**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_watcher.py tests/test_api.py -q`
Expected: PASS. Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add splitstep/watcher.py splitstep/api/routes.py tests/test_watcher.py tests/test_api.py
git commit -m "feat: the inbox says what it is ignoring"
```

---

### Task 10: Per-library clip colour profile (migration 011)

The carefully guarded one. Read `docs/superpowers/specs/2026-08-21-clip-colour-metadata-design.md` and the comment block at `splitstep/media/transcode.py:202-278` before touching anything. Two invariants survive unchanged: **untagged sources are still refused** (a relabel without a conversion is worse than a refusal), and **mixed-profile inputs are still refused** (this ffmpeg has no working tonemap in either direction). The only thing that changes is *which* profile is enforced: a value locked per library instead of a module constant pinned to one specific iPhone.

**Files:**
- Create: `splitstep/db/migrations/011_settings.sql`, `splitstep/db/settings.py`
- Modify: `splitstep/media/transcode.py:235-278` (constants → `HLG_PROFILE`, parameterize `_require_locked_color`), `:281-315,435-438` (`make_clip` signature and encode flags), `splitstep/jobs/handlers.py:331-380` (`handle_clip` resolves the profile)
- Test: `tests/test_db.py` (settings), `tests/test_clips.py` (append)

**Interfaces:**
- Produces: `HLG_PROFILE: tuple[str, str, str, str]` in canonical order `(range, space, trc, primaries)` — exactly the order `_require_locked_color` already compares in; `db/settings.py`: `get_setting`, `set_setting`, `get_color_profile(conn) -> tuple | None`, `lock_color_profile(conn, profile) -> None`; `make_clip(…, color_profile: tuple = HLG_PROFILE)`; `handlers._clip_color_profile(conn, library, info, src) -> tuple`. Phase 4's numbered-render intermediates encode at the same locked profile via the same argument.

- [ ] **Step 1: Migration and settings module, with tests**

Create `splitstep/db/migrations/011_settings.sql`:

```sql
-- Per-library key/value settings. First key: clip_color_profile, locking
-- the clip colour profile to the library instead of to a module constant
-- pinned to one specific phone. No backfill: resolution at first export
-- handles pre-existing libraries (see handlers._clip_color_profile).
CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
```

Create `splitstep/db/settings.py`:

```python
"""Per-library key/value settings (migration 011).

One table, JSON values where a value has structure. The clip colour profile
lives here rather than in code because it is a fact about the library's
existing clips -- the thing `-c copy` concat has to stay compatible with --
not a fact about the app.
"""

import json
import sqlite3

CLIP_COLOR_PROFILE_KEY = "clip_color_profile"


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def get_color_profile(conn: sqlite3.Connection) -> tuple[str, str, str, str] | None:
    raw = get_setting(conn, CLIP_COLOR_PROFILE_KEY)
    if raw is None:
        return None
    values = json.loads(raw)
    return (values[0], values[1], values[2], values[3])


def lock_color_profile(conn: sqlite3.Connection, profile: tuple[str, str, str, str]) -> None:
    set_setting(conn, CLIP_COLOR_PROFILE_KEY, json.dumps(list(profile)))
```

Append to `tests/test_db.py`:

```python
from splitstep.db.settings import get_color_profile, lock_color_profile


def test_color_profile_round_trips_and_starts_unset(conn):
    assert get_color_profile(conn) is None
    lock_color_profile(conn, ("tv", "bt709", "bt709", "bt709"))
    assert get_color_profile(conn) == ("tv", "bt709", "bt709", "bt709")
```

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_db.py -q` → PASS after implementing.

- [ ] **Step 2: Parameterize the transcode layer**

In `splitstep/media/transcode.py`:

1. Below the four `CLIP_COLOR_*` constants (line 238), add:

```python
# Canonical tuple order everywhere a profile travels: (range, space, trc,
# primaries) -- the order _require_locked_color has always compared in.
HLG_PROFILE = (CLIP_COLOR_RANGE, CLIP_COLOR_SPACE, CLIP_COLOR_TRC, CLIP_COLOR_PRIMARIES)
```

2. Change `_require_locked_color` to take the profile and rewrite the message (the iPhone "HDR off" advice was backwards — following it produces footage this function rejects):

```python
def _require_locked_color(
    info: MediaInfo, src: Path, profile: tuple[str, str, str, str]
) -> None:
    """Refuse a source whose colour metadata is not this library's profile.

    Strict equality on all four fields, and `None` -- an untagged source --
    fails it exactly as a mismatched one does. That is deliberate: untagged
    pixels are unknown pixels, so applying the profile's tags to them would
    be a relabel without a conversion, which produces a file that looks
    correct while being wrong. Harder to find later than an honest mismatch.

    Refused rather than converted because this ffmpeg cannot convert:
    measured on 9.0.1 with neither libzimg nor libplacebo, `zscale` is
    absent so no linear-light stage exists to feed `tonemap`, and the
    built-in `colorspace` filter takes HLG neither in nor out. There is
    deliberately no override -- an override is a way to write a permanently
    wrong clip, and the clip is the artifact that has to stay
    concat-compatible for years.
    """
    actual = (info.color_range, info.color_space, info.color_transfer, info.color_primaries)
    if actual == profile:
        return

    shown = tuple(field or "unset" for field in actual)
    raise TranscodeError(
        f"{src.name} does not match this library's clip colour profile, so a clip "
        f"cut from it could not be concatenated with the ones already cut.\n"
        f"  source:  range={shown[0]} space={shown[1]} transfer={shown[2]} "
        f"primaries={shown[3]}\n"
        f"  library: range={profile[0]} space={profile[1]} transfer={profile[2]} "
        f"primaries={profile[3]}\n"
        f"The profile locked to the first clip this library exported, and no "
        f"conversion was attempted: this ffmpeg has no working tonemap in either "
        f"direction. Record with the same camera settings as that first export "
        f"(on an iPhone, Settings > Camera > Record Video > HDR Video ON records "
        f"HLG), or keep this footage in its own library."
    )
```

3. `make_clip`: add keyword `color_profile: tuple[str, str, str, str] = HLG_PROFILE` to the signature; the call at line 315 becomes `_require_locked_color(info, src, color_profile)`; the encode flags at lines 435-438 become:

```python
            "-color_range", color_profile[0],
            "-colorspace", color_profile[1],
            "-color_primaries", color_profile[3],
            "-color_trc", color_profile[2],
```

(Note the index order: the tuple is `(range, space, trc, primaries)`; the flag order in the vector stays as it was.)

- [ ] **Step 3: Resolution in the handler, with tests first**

Append to `tests/test_clips.py` (uses `types.SimpleNamespace` as a stand-in — the resolver only touches the four colour attributes):

```python
from types import SimpleNamespace

from splitstep.db.settings import get_color_profile, lock_color_profile
from splitstep.jobs.handlers import _clip_color_profile
from splitstep.media.transcode import HLG_PROFILE, TranscodeError


def _info(range_="tv", space="bt709", trc="bt709", primaries="bt709"):
    return SimpleNamespace(color_range=range_, color_space=space,
                           color_transfer=trc, color_primaries=primaries)


def test_first_export_locks_the_library_to_the_source(library, conn, tmp_path):
    profile = _clip_color_profile(conn, library, _info(), tmp_path / "a.mov")
    assert profile == ("tv", "bt709", "bt709", "bt709")
    assert get_color_profile(conn) == profile


def test_a_stored_profile_wins_over_the_source(library, conn, tmp_path):
    lock_color_profile(conn, HLG_PROFILE)
    profile = _clip_color_profile(conn, library, _info(), tmp_path / "a.mov")
    assert profile == HLG_PROFILE  # the mismatch is _require_locked_color's to refuse


def test_pre_migration_clips_lock_the_legacy_hlg_profile(library, conn, tmp_path):
    # A library with clips on disk but no settings row predates migration
    # 011. Every such clip was cut under the module-pinned constants, so
    # HLG is a fact about those files, not a guess.
    clips = library.clips_dir("2026-08-18")
    clips.mkdir(parents=True)
    (clips / "01-1000-2000.mp4").write_bytes(b"x")
    profile = _clip_color_profile(conn, library, _info(), tmp_path / "a.mov")
    assert profile == HLG_PROFILE
    assert get_color_profile(conn) == HLG_PROFILE


def test_an_untagged_source_cannot_become_the_profile(library, conn, tmp_path):
    with pytest.raises(TranscodeError, match="untagged"):
        _clip_color_profile(conn, library, _info(range_=None, space=None),
                            tmp_path / "a.mov")
    assert get_color_profile(conn) is None
```

Run to verify failure, then implement in `splitstep/jobs/handlers.py` (above `handle_clip`; add imports `from splitstep.db.settings import get_color_profile, lock_color_profile` and extend the transcode import with `HLG_PROFILE` and `TranscodeError`):

```python
def _clip_color_profile(conn, library: Library, info, src: Path):
    """The colour profile this library's clips are locked to, resolving and
    locking it on first use.

    Priority: the stored setting; else, if clips already exist on disk, the
    legacy module-pinned HLG profile (every pre-migration clip was cut under
    it, so it is a fact about those files, not a guess); else this source's
    own tags, which become the library's profile permanently. Locked at
    check time rather than after a successful encode: the worker is
    single-threaded so nothing races it, and a first export that fails
    mid-encode for an unrelated reason still locked a profile read from
    valid tags -- the owner's camera either way.

    An untagged source can never become the profile: unknown pixels locking
    the library would bless every future untagged source, exactly the
    relabel-without-conversion _require_locked_color exists to refuse.
    """
    stored = get_color_profile(conn)
    if stored is not None:
        return stored
    if any(library.sessions_dir.glob("*/clips/*.mp4")):
        lock_color_profile(conn, HLG_PROFILE)
        return HLG_PROFILE
    actual = (info.color_range, info.color_space, info.color_transfer,
              info.color_primaries)
    if None in actual:
        shown = tuple(field or "unset" for field in actual)
        raise TranscodeError(
            f"{src.name} is missing colour metadata (untagged: range={shown[0]} "
            f"space={shown[1]} transfer={shown[2]} primaries={shown[3]}), so it "
            f"cannot set this library's clip colour profile. Export a properly "
            f"tagged source first."
        )
    lock_color_profile(conn, actual)
    return actual
```

In `handle_clip`, before the `make_clip` call (line 379), add:

```python
    # Probing here and again inside make_clip is two ffprobe calls (~50 ms
    # each) against minutes of encode -- cheaper than widening make_clip's
    # signature to take a pre-probed MediaInfo.
    profile = _clip_color_profile(conn, library, probe(src), src)
```

and pass it: `make_clip(src, dst, start_ms=start_ms, end_ms=end_ms, rotation_deg=source["rotation_deg"], on_progress=progress, color_profile=profile)`. Add `probe` to the handler module's existing `from splitstep.media.probe import display_size, probe` import (already present — verify).

- [ ] **Step 4: Sweep tests pinned to the old message**

Run: `grep -rn "HDR Video turned off\|does not carry the locked profile" tests/`
Update every hit to the new phrasing (`does not match this library's clip colour profile`); the behavior they assert (refusal, no partial file) is unchanged.

- [ ] **Step 5: Run the full suite**

Run: `~/miniconda3/envs/splitstep/bin/python -m pytest -q`
Expected: PASS — existing fixture sources are HLG-tagged (`hlg_setparams`), an empty test library locks to them on first export, and `make_clip`'s default keeps direct callers green. Ruff clean.

- [ ] **Step 6: Commit**

```bash
git add splitstep/db/migrations/011_settings.sql splitstep/db/settings.py \
  splitstep/media/transcode.py splitstep/jobs/handlers.py \
  tests/test_db.py tests/test_clips.py
git commit -m "feat(media): lock the clip colour profile per library, not per phone

The four-constant profile was pinned to one specific iPhone's HLG. A
library now locks to its own first export; pre-migration libraries with
clips on disk lock to the legacy HLG those clips were cut under. Untagged
and mismatched sources are refused exactly as before -- only WHICH profile
is enforced changed."
```

---

### Task 11: Docs truth pass + delete `label.html`

**Files:**
- Delete: `web/public/label.html`
- Modify: `README.md` (lines 70-79 command list, 83-86 pipeline claim, 230-231 reels claim, 256 proxy claim, 271 capture settings), `CLAUDE.md` (line 20 test count, lines 36-39 library-path claim), `docs/superpowers/specs/2026-08-26-mac-app-distribution-design.md` (migration number)

No tests — docs and a file deletion. Verify claims against the code you just wrote, not from memory.

- [ ] **Step 1: Delete the dead labelling page**

```bash
git rm web/public/label.html
```

(It ships live at `/label.html`, hardcoded to one 2026-08-18 proxy and a pre-rename localStorage key. Superseded by in-app label mode. `web/dist` is gitignored; the stale copy there disappears on the next `npm run build`.)

- [ ] **Step 2: Fix README.md**

1. Line 271 area: change `HDR off` to `HDR on` in the capture recipe, with the reason: HLG is what the clip profile locks to on first export; HDR off records bt709 SDR, which the exporter refuses against an HLG-locked library.
2. Lines 83-86: rewrite the watcher paragraph to match the real pipeline: ingest registers only (seconds); proxy build and detection wait for the setup wizard (rotation + play region); statuses `ingesting → needs_setup → building → ingested → detecting → ready`.
3. Lines 70-79: add the missing command groups to the list: `setup`, `labels export|score`, `clips export|orphans|prune`, `config show|set-library`.
4. Lines 230-231: delete the "Still deferred: reel building" claim (reels shipped: migration 007, `handle_reel`, `/reels` routes).
5. Line 256: delete the "still genuinely unverified: 4K-derived proxy playback" claim (verified 2026-08-23, see `docs/superpowers/specs/2026-08-23-ui-design-audit.md`).
6. Add a short paragraph to the setup/run section: `--library` is now optional — resolution order flag → `SPLITSTEP_LIBRARY` → `splitstep config set-library`.

- [ ] **Step 3: Fix CLAUDE.md**

1. Line 20: update the test-count comment — get the real number first: `~/miniconda3/envs/splitstep/bin/python -m pytest --collect-only -q 2>/dev/null | tail -1` and use what it prints.
2. Lines 36-39: replace "library path is required on every command; there is no default" with the new resolution order (flag → `SPLITSTEP_LIBRARY` → config file via `splitstep config set-library`), keeping the examples.

- [ ] **Step 4: Fix the spec's stale migration number**

In `docs/superpowers/specs/2026-08-26-mac-app-distribution-design.md`, Phase 4 section: change `(migration 008)` to `(next free migration number — 010 and 011 were taken by Phase 1)`.

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md docs/superpowers/specs/2026-08-26-mac-app-distribution-design.md
git commit -m "docs: the README stops contradicting the pipeline and the exporter

HDR off was exactly the setting that produces footage clip export
refuses. Also: real pipeline (ingest stalls at needs_setup on purpose),
full command surface, reels are shipped, label.html is gone."
```

---

### Task 12: Final verification

- [ ] **Step 1: Full suite, ruff, and a live smoke**

```bash
~/miniconda3/envs/splitstep/bin/python -m pytest -q
~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
```

Expected: all green, no warnings (the suite runs with `filterwarnings=error`, so the two new dependencies are also being vetted here).

Smoke the new surface end to end against a throwaway library:

```bash
SPLITSTEP_LIBRARY=$(mktemp -d) ~/miniconda3/envs/splitstep/bin/splitstep serve --create --port 8421 &
sleep 3
curl -s http://127.0.0.1:8421/api/inbox
curl -s -X POST http://127.0.0.1:8421/api/import -F 'file=@tests/fixtures/ground_level_source01.jsonl;filename=x.txt' | head -c 200   # expect a 415 body
kill %1
```

Expected: `serve --create` initializes and serves; `/api/inbox` returns `{"unsupported": [], "failed": []}`; the bogus import is refused with 415.

- [ ] **Step 2: Verify the plan's own checkboxes are all ticked, then hand off**

Phase 1 complete. Phase 2 (friend-mode UI) gets its own plan, written against these routes as landed. Do not start Phase 2 from this document.

**Live-library note (for Steven, not the agent):** migrations 010/011 apply to the real library on the next `serve` start. Follow the ritual in memory `live-library-migration-ritual`: back up `library.db` with sqlite `.backup` first, restart serve, verify counts.
