# Phase 3 — Tauri shell, PyInstaller sidecar, `.dmg` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce an unsigned arm64 `.dmg` that a non-technical friend can copy, open, point at a folder, and use — no terminal, no Homebrew, no conda, no npm.

**Architecture:** A Tauri v2 shell renders a **front layer** (library chooser) from its own asset bundle, because the Python server cannot serve a page asking which library it should open. Once a library is chosen the shell spawns a PyInstaller-frozen `splitstep serve --library <path> [--create] --port <n>` on an OS-assigned port, health-checks `GET /api/config`, and points the webview at `http://127.0.0.1:<port>`. The existing Svelte app is untouched and keeps being served by Python.

**Tech Stack:** Tauri v2 (Rust 1.98, cargo 1.98), PyInstaller 6.22 onedir, Vite 6 multi-config, Svelte 5, static ffmpeg from evermeet.cx.

**Design spec:** `docs/superpowers/specs/2026-08-27-phase3-tauri-shell-design.md`. Read it before Task 1.

## Global Constraints

- **arm64 only.** No universal binary. Every build command targets `aarch64-apple-darwin`.
- **Unsigned.** No `signingIdentity` in `tauri.conf.json`. Gatekeeper workaround documented in the README.
- **Python is invoked by interpreter path**, never bare: `~/miniconda3/envs/splitstep/bin/python -m pytest -q`. Bare `pytest` in a worktree silently imports the wrong checkout.
- **`ruff` line-length is 100.** `~/miniconda3/envs/splitstep/bin/ruff check splitstep tests` must pass.
- **`pytest` runs with `filterwarnings = ["error"]`.** A new warning fails the suite.
- **All frontend logic lives in `web/src/lib/` with vitest coverage.** Components are thin shells. Logic in a `.svelte` file is untestable — jsdom has no `<video>`.
- **Design tokens only.** Colours and type sizes come from `web/src/app.css`'s `@theme` block. No raw Tailwind palette steps (`bg-neutral-800`), no arbitrary sizes (`text-[11px]`). Roles: `bg`/`surface`/`surface-2`/`line`, text `fg`/`dim`/`faint`, semantic `star`/`point`/`accent`/`danger`; type `display`/`title`/`body`/`data`/`caption`; `font-data` carries `tabular-nums` and every count, size and path belongs in it.
- **`web/dist` must stay byte-identical to what `npm run build` produces today**, because it ships inside the sidecar. The launcher builds to `web/dist-launcher/` via a separate config.
- **Baseline suites, green before and after every task:** 837 pytest, 628 vitest, `svelte-check` 0 errors.
- **Never touch the live library** at `/Volumes/SanDisk_2TB/SplitStep` from a build or a test.
- **Bundle payload is flat at `sys._MEIPASS`**, the layout `splitstep/resources.py` alone writes down: `ffmpeg`, `ffprobe`, `yolo11n.pt`, `font.ttf`, `web_dist/`.

---

### Task 1: Freeze the sidecar

The riskiest task in the phase, so it goes first. torch and ultralytics under PyInstaller are a known-wrinkly path: both do runtime imports PyInstaller's static analysis cannot see, and ultralytics ships YAML data files it loads by relative path.

**Files:**
- Create: `packaging/requirements-frozen.txt`
- Create: `packaging/splitstep.spec`
- Create: `packaging/README.md`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: `packaging/dist/splitstep-server/splitstep-server` — a onedir executable taking the same argv as `splitstep serve`. Task 2 fills its payload; Task 6 spawns it.

- [ ] **Step 1: Pin the environment that is known to work**

The env this repo was validated against is the source of truth. Do not re-resolve.

```bash
~/miniconda3/envs/splitstep/bin/pip freeze > packaging/requirements-frozen.txt
```

- [ ] **Step 2: Strip the editable self-reference**

`pip freeze` emits the editable install of splitstep itself as a path or a `-e` line. PyInstaller analyses the source tree directly, so that line is noise that breaks a clean `pip install -r`.

```bash
grep -v -i '^-e \|splitstep' packaging/requirements-frozen.txt > /tmp/rf && mv /tmp/rf packaging/requirements-frozen.txt
head -5 packaging/requirements-frozen.txt
```

Expected: version-pinned lines like `fastapi==0.115.x`, no `splitstep` line.

- [ ] **Step 3: Write the PyInstaller spec**

Create `packaging/splitstep.spec`:

```python
# PyInstaller spec for the frozen `splitstep serve` sidecar.
#
# onedir, not onefile: onefile unpacks ~2 GB to a temp directory on every
# launch, which is both slow and a second copy of the bundle on a disk the
# user did not agree to fill. onedir also lets `sys._MEIPASS` be a real
# directory the app can read from directly, which is what resources.py
# already assumes.
#
# The datas list is intentionally empty here -- Task 2 fills it. Keeping the
# freeze and its payload as two commits means a hook failure and a missing
# binary cannot be confused for each other.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

REPO = Path(SPECPATH).parent

# ultralytics loads model YAMLs (`yolo11n.yaml` and the cfg/ tree) by
# relative path at runtime, and torch's dynamo/inductor subpackages are
# imported lazily by name. Static analysis sees neither, so both are
# collected wholesale. This is the "budget a day of hook-fighting" the
# distribution spec warned about; collecting broadly is the cheap answer.
datas = collect_data_files("ultralytics")
hiddenimports = (
    collect_submodules("ultralytics")
    + collect_submodules("splitstep")
    # uvicorn resolves its protocol implementations through a string
    # registry, so none of them appear as imports.
    + [
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
    ]
)

a = Analysis(
    [str(REPO / "packaging" / "entry.py")],
    pathex=[str(REPO)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # torchvision pulls a large tree we never touch -- detection is
    # ultralytics' own pipeline over cv2 -- and excluding it removes a
    # frequent source of PyInstaller import errors.
    excludes=["torchvision", "matplotlib", "tkinter", "PyQt5", "PySide2"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="splitstep-server",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    target_arch="arm64",
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, name="splitstep-server",
)
```

- [ ] **Step 4: Write the frozen entry point**

Create `packaging/entry.py`:

```python
"""Entry point for the frozen sidecar.

Not `splitstep.cli:main` directly: the shell always invokes this as a
server, and hardcoding `serve` here means the Tauri side cannot accidentally
run `init` or `detect` against a library by passing stray argv. It also
gives PyInstaller a single, static module to analyse.
"""

import sys

from splitstep.cli import main

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "serve", *sys.argv[1:]]
    sys.exit(main())
```

- [ ] **Step 5: Ignore build output**

Append to `.gitignore`:

```
# PyInstaller output -- 2 GB of derived bytes
packaging/build/
packaging/dist/
```

- [ ] **Step 6: Run the freeze**

```bash
cd packaging && ~/miniconda3/envs/splitstep/bin/pyinstaller --noconfirm --distpath dist --workpath build splitstep.spec 2>&1 | tail -30
```

Expected: ends with `Building COLLECT COLLECT-00.toc completed successfully.` If it fails on a missing module, add that module to `hiddenimports` and re-run — that is the expected loop, not a surprise.

- [ ] **Step 7: Verify the frozen binary boots and answers**

It has no library yet, so it must fail with the *friendly* unconfigured message, not a traceback. That proves the whole import graph resolved.

```bash
./dist/splitstep-server/splitstep-server --help 2>&1 | head -5
```

Expected: argparse usage text for `serve`, exit 0. A `ModuleNotFoundError` here means Step 6's loop is not done.

- [ ] **Step 8: Verify it serves a real library**

```bash
mkdir -p /tmp/ss-freeze-test && ./dist/splitstep-server/splitstep-server --library /tmp/ss-freeze-test --create --port 8899 &
sleep 15 && curl -s localhost:8899/api/config && kill %1
```

Expected: JSON containing `"mode"`. This is the first proof the frozen server is real.

- [ ] **Step 9: Commit**

```bash
git add packaging .gitignore
git commit -m "build: freeze the server with PyInstaller"
```

---

### Task 2: Fill the bundle payload

**Files:**
- Create: `packaging/fetch_assets.sh`
- Modify: `packaging/splitstep.spec` (the `datas` list)
- Create: `packaging/vendor/` (gitignored; holds the fetched binaries)
- Modify: `.gitignore`

**Interfaces:**
- Consumes: Task 1's `packaging/splitstep.spec`.
- Produces: a frozen tree where `resources.ffmpeg_exe()`, `ffprobe_exe()`, `yolo_weights()`, `overlay_font()` and `spa_dist()` all resolve inside `sys._MEIPASS` with no PATH fallback.

- [ ] **Step 1: Write the asset fetcher**

Create `packaging/fetch_assets.sh`:

```bash
#!/bin/bash
# Fetch the third-party binaries the bundle ships. Not committed to git --
# they are large, and their licences are satisfied by the credits note plus
# the public repo, not by vendoring.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p vendor && cd vendor

# evermeet.cx publishes static macOS builds. -display_rotation needs 7.0+;
# make_proxy passes it on every transcode, so an older build breaks rotation
# silently rather than loudly.
for tool in ffmpeg ffprobe; do
  if [ ! -x "$tool" ]; then
    curl -fL "https://evermeet.cx/ffmpeg/getrelease/$tool/zip" -o "$tool.zip"
    unzip -o "$tool.zip" && rm "$tool.zip"
    chmod +x "$tool"
  fi
done

# An OFL face for the numbered-reel overlay. resources.overlay_font() looks
# for `font.ttf` in the bundle first and falls back to macOS system faces --
# the fallback works today, but a bundled face is what makes the render
# identical on a machine whose Supplemental fonts were never installed.
if [ ! -f font.ttf ]; then
  curl -fL "https://github.com/googlefonts/roboto/releases/download/v2.138/roboto-android.zip" -o roboto.zip
  unzip -oj roboto.zip "Roboto-Bold.ttf" -d . && rm roboto.zip
  mv Roboto-Bold.ttf font.ttf
fi

# The detector weights. Committed nowhere (gitignored at the repo root) but
# present in the dev checkout after the first run.
if [ ! -f yolo11n.pt ]; then cp ../../yolo11n.pt . ; fi

echo "vendor/ ready:" && ls -la
./ffmpeg -version | head -1
```

- [ ] **Step 2: Make it executable and run it**

```bash
chmod +x packaging/fetch_assets.sh && ./packaging/fetch_assets.sh
```

Expected: last line reports `ffmpeg version 7.x` or newer.

- [ ] **Step 3: Verify the ffmpeg version floor**

```bash
./packaging/vendor/ffmpeg -h filter=overlay >/dev/null && echo "overlay OK"
./packaging/vendor/ffmpeg -hide_banner -h 2>&1 | grep -c display_rotation || echo "MISSING display_rotation"
```

Expected: `overlay OK`. If `display_rotation` is missing the build is too old — `make_proxy` depends on it and rotation would silently break.

- [ ] **Step 4: Add the payload to the spec**

In `packaging/splitstep.spec`, replace the `datas = collect_data_files("ultralytics")` line with:

```python
VENDOR = REPO / "packaging" / "vendor"
WEB_DIST = REPO / "web" / "dist"

# Flat at the bundle root, because resources.py's _bundled() looks for bare
# names there. web_dist/ is the one nested entry, matching spa_dist().
datas = collect_data_files("ultralytics") + [
    (str(VENDOR / "yolo11n.pt"), "."),
    (str(VENDOR / "font.ttf"), "."),
    (str(WEB_DIST), "web_dist"),
]
```

And replace `binaries=[],` with:

```python
    # ffmpeg/ffprobe go in binaries, not datas: PyInstaller runs macholib
    # over binaries and will not mangle a static executable, whereas a data
    # file loses its executable bit in some COLLECT paths.
    binaries=[
        (str(VENDOR / "ffmpeg"), "."),
        (str(VENDOR / "ffprobe"), "."),
    ],
```

- [ ] **Step 5: Ignore the vendor tree**

Append to `.gitignore`:

```
packaging/vendor/
```

- [ ] **Step 6: Build the web bundle, then re-freeze**

```bash
cd web && npm run build && cd .. && cd packaging && ~/miniconda3/envs/splitstep/bin/pyinstaller --noconfirm --distpath dist --workpath build splitstep.spec 2>&1 | tail -5
```

Expected: `completed successfully`.

- [ ] **Step 7: Verify every resource resolves inside the bundle**

```bash
cd packaging && ls dist/splitstep-server/_internal/{ffmpeg,ffprobe,yolo11n.pt,font.ttf} dist/splitstep-server/_internal/web_dist/index.html
```

Expected: all five paths listed, no `No such file`.

- [ ] **Step 8: Verify the frozen app serves the real UI**

```bash
cd packaging && ./dist/splitstep-server/splitstep-server --library /tmp/ss-freeze-test --port 8899 &
sleep 15 && curl -s -o /dev/null -w "%{http_code}\n" localhost:8899/ && kill %1
```

Expected: `200`, not `503`. A 503 means `spa_dist()` did not find `web_dist/`.

- [ ] **Step 9: Commit**

```bash
git add packaging .gitignore
git commit -m "build: ship ffmpeg, weights, font and the SPA inside the freeze"
```

---

### Task 3: Config remembers known libraries

**Files:**
- Modify: `splitstep/appconfig.py`
- Test: `tests/test_appconfig.py`

**Interfaces:**
- Consumes: `appconfig.load_config()`, `appconfig.save_config()`.
- Produces: `known_libraries() -> list[str]` (most-recent-first) and
  `remember_library(path: str | Path) -> None`, called by `splitstep config
  set-library`. The Rust side reads and writes the same JSON directly (it runs
  before any Python exists), so these two keep the CLI writing the same shape.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_appconfig.py`. The file's `isolated_config` fixture is
`autouse=True`, so it points `config_path()` into tmp without being named:

```python
def test_known_libraries_is_empty_when_unset():
    assert appconfig.known_libraries() == []


def test_remember_library_puts_newest_first():
    appconfig.remember_library("/a")
    appconfig.remember_library("/b")
    assert appconfig.known_libraries() == ["/b", "/a"]


def test_remember_library_dedupes_and_promotes():
    appconfig.remember_library("/a")
    appconfig.remember_library("/b")
    appconfig.remember_library("/a")
    assert appconfig.known_libraries() == ["/a", "/b"]


def test_remember_library_stores_absolute_paths():
    appconfig.remember_library("~/Movies/SplitStep")
    assert appconfig.known_libraries() == [
        str(Path("~/Movies/SplitStep").expanduser())
    ]


def test_known_libraries_tolerates_a_corrupt_config():
    # Same contract get_mode() already has: a config too broken to parse
    # must not strand the chooser, because the chooser is the one screen
    # that can fix it.
    appconfig.config_path().write_text("{not json")
    assert appconfig.known_libraries() == []


def test_known_libraries_tolerates_a_non_list_value():
    appconfig.save_config({"libraries": "nonsense"})
    assert appconfig.known_libraries() == []


def test_remember_library_leaves_other_keys_alone():
    appconfig.set_mode("friend")
    appconfig.remember_library("/a")
    assert appconfig.get_mode() == "friend"
```

- [ ] **Step 2: Run them and watch them fail**

```bash
~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_appconfig.py -q 2>&1 | tail -5
```

Expected: FAIL, `AttributeError: module 'splitstep.appconfig' has no attribute 'known_libraries'`.

- [ ] **Step 3: Implement**

Append to `splitstep/appconfig.py`:

```python
def known_libraries() -> list[str]:
    """Libraries opened before, most recent first.

    Tolerant of a corrupt or absent key for the same reason get_mode() is:
    the front layer is the one screen that can repair a bad config, so it
    must be able to render against one. A non-list value collapses to empty
    rather than raising -- a hand-edited typo should cost the list, not the
    app.
    """
    try:
        value = load_config().get("libraries", [])
    except LibraryUnconfigured:
        return []
    if not isinstance(value, list):
        return []
    return [str(entry) for entry in value if isinstance(entry, str)]


def remember_library(path: str | Path) -> None:
    """Record a library as opened, newest first, deduped.

    Paths are stored expanded and absolute: the list is displayed to a human
    picking between drives, and `~/Movies/SplitStep` alongside
    `/Users/x/Movies/SplitStep` would read as two libraries when it is one.
    Unreachable entries are deliberately NOT pruned here -- an unplugged
    drive must not be forgotten, and reachability is decided at render time.
    """
    resolved = str(Path(path).expanduser())
    try:
        cfg = load_config()
    except LibraryUnconfigured:
        cfg = {}
    existing = [entry for entry in known_libraries() if entry != resolved]
    cfg["libraries"] = [resolved, *existing]
    save_config(cfg)
```

- [ ] **Step 4: Run the tests**

```bash
~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_appconfig.py -q 2>&1 | tail -3
```

Expected: all pass.

- [ ] **Step 5: Give the helpers a caller**

Without this the two functions are dead code. `splitstep config set-library`
and the shell's chooser both set the current library, so they must both add
to the same list — otherwise a library reached from the CLI is invisible in
the chooser, and the list quietly means "libraries opened from the app" while
claiming to mean "libraries you have opened".

In `splitstep/cli.py`, inside `cmd_config_set_library`, add a call to
`appconfig.remember_library(...)` with the same path it saves, immediately
after the save.

Add to `tests/test_appconfig.py`:

```python
def test_set_library_from_the_cli_joins_the_known_list():
    from splitstep.cli import main

    assert main(["config", "set-library", "/tmp/some-library"]) == 0
    assert "/tmp/some-library" in appconfig.known_libraries()
```

Run it:

```bash
~/miniconda3/envs/splitstep/bin/python -m pytest tests/test_appconfig.py -q 2>&1 | tail -3
```

Expected: all pass. If `main` does not accept an argv list, call the
subcommand function directly with an `argparse.Namespace` instead — check the
signature rather than guessing.

- [ ] **Step 6: Run the full suite and the linter**

```bash
~/miniconda3/envs/splitstep/bin/python -m pytest -q 2>&1 | tail -3 && ~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
```

Expected: `845 passed`, `All checks passed!`.

- [ ] **Step 7: Commit**

```bash
git add splitstep/appconfig.py tests/test_appconfig.py
git commit -m "feat(config): remember the libraries that have been opened"
```

---

### Task 4: Launcher logic

Pure TypeScript, no Svelte, per the house rule. This is everything the front layer decides; Task 5 only renders it.

**Files:**
- Create: `web/src/lib/launcher.ts`
- Test: `web/tests/launcher.test.ts`

**Interfaces:**
- Consumes: `formatBytes` from `web/src/lib/reels.ts`.
- Produces:
  - `type LibraryEntry = { path: string; reachable: boolean; hasLibrary: boolean }`
  - `type ChooserState = 'first-run' | 'not-connected' | 'switching'`
  - `chooserState(known: LibraryEntry[], configured: string | null): ChooserState`
  - `chooserCopy(state: ChooserState, configured: string | null): { heading: string; body: string }`
  - `defaultLibraryPath(home: string): string`
  - `describeTarget(free: number | null, hasLibrary: boolean): string`
  - `sortEntries(entries: LibraryEntry[]): LibraryEntry[]`

- [ ] **Step 1: Write the failing tests**

Create `web/tests/launcher.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import {
  chooserCopy,
  chooserState,
  defaultLibraryPath,
  describeTarget,
  sortEntries,
  type LibraryEntry,
} from '../src/lib/launcher'

const entry = (path: string, over: Partial<LibraryEntry> = {}): LibraryEntry => ({
  path, reachable: true, hasLibrary: true, ...over,
})

describe('chooserState', () => {
  it('is first-run when nothing has ever been configured', () => {
    expect(chooserState([], null)).toBe('first-run')
  })

  it('is not-connected when the configured library is unreachable', () => {
    expect(chooserState([entry('/v/ssd', { reachable: false })], '/v/ssd'))
      .toBe('not-connected')
  })

  it('is switching when the configured library is reachable', () => {
    // Reaching the chooser with a working library means the user asked for
    // it from Settings -- there is no other way in.
    expect(chooserState([entry('/v/ssd')], '/v/ssd')).toBe('switching')
  })

  it('is first-run when known libraries exist but none is configured', () => {
    expect(chooserState([entry('/v/ssd')], null)).toBe('first-run')
  })
})

describe('chooserCopy', () => {
  it('explains why the setting exists on first run', () => {
    const copy = chooserCopy('first-run', null)
    expect(copy.heading).toMatch(/where/i)
    expect(copy.body).toMatch(/external drive/i)
  })

  it('names the missing path when not connected', () => {
    const copy = chooserCopy('not-connected', '/Volumes/SanDisk_2TB/SplitStep')
    expect(copy.body).toContain('/Volumes/SanDisk_2TB/SplitStep')
  })

  it('does not claim a path is missing when switching', () => {
    expect(chooserCopy('switching', '/v/ssd').body).not.toMatch(/not connected/i)
  })
})

describe('defaultLibraryPath', () => {
  it('suggests Movies, the macOS home for video', () => {
    expect(defaultLibraryPath('/Users/steven')).toBe('/Users/steven/Movies/SplitStep')
  })

  it('tolerates a trailing slash on home', () => {
    expect(defaultLibraryPath('/Users/steven/')).toBe('/Users/steven/Movies/SplitStep')
  })
})

describe('describeTarget', () => {
  it('says a folder will be opened when it already holds a library', () => {
    expect(describeTarget(500_000_000_000, true)).toMatch(/open/i)
  })

  it('says a folder will be created when it does not', () => {
    expect(describeTarget(500_000_000_000, false)).toMatch(/create/i)
  })

  it('reports free space so an external drive is an informed choice', () => {
    expect(describeTarget(500_000_000_000, false)).toContain('500 GB')
  })

  it('omits free space rather than guessing when it is unknown', () => {
    const text = describeTarget(null, false)
    expect(text).not.toMatch(/free/i)
    expect(text).toMatch(/create/i)
  })
})

describe('sortEntries', () => {
  it('keeps recency order but sinks unreachable entries', () => {
    const sorted = sortEntries([
      entry('/a', { reachable: false }),
      entry('/b'),
      entry('/c'),
    ])
    expect(sorted.map((e) => e.path)).toEqual(['/b', '/c', '/a'])
  })

  it('does not mutate its input', () => {
    const input = [entry('/a', { reachable: false }), entry('/b')]
    sortEntries(input)
    expect(input[0].path).toBe('/a')
  })
})
```

- [ ] **Step 2: Run and watch it fail**

```bash
cd web && npx vitest run tests/launcher.test.ts 2>&1 | tail -5
```

Expected: FAIL, `Failed to resolve import "../src/lib/launcher"`.

- [ ] **Step 3: Implement**

Create `web/src/lib/launcher.ts`:

```typescript
import { formatBytes } from './reels'

export type LibraryEntry = {
  path: string
  reachable: boolean
  hasLibrary: boolean
}

export type ChooserState = 'first-run' | 'not-connected' | 'switching'

/** Which of the three reasons brought us to the chooser.
 *
 * The chooser is skipped on a normal launch, so arriving here always means
 * one of exactly three things, and the copy differs for each. `switching` is
 * inferred rather than passed: a reachable configured library means the shell
 * would have gone straight into the app, so a human must have asked for the
 * chooser from Settings.
 */
export function chooserState(
  known: LibraryEntry[],
  configured: string | null,
): ChooserState {
  if (!configured) return 'first-run'
  const match = known.find((entry) => entry.path === configured)
  return match?.reachable ? 'switching' : 'not-connected'
}

export function chooserCopy(
  state: ChooserState,
  configured: string | null,
): { heading: string; body: string } {
  if (state === 'not-connected') {
    return {
      heading: 'Your library is not connected',
      body:
        `SplitStep last used ${configured}, which is not available right now. ` +
        'Plug the drive in and try again, or choose somewhere else.',
    }
  }
  if (state === 'switching') {
    return {
      heading: 'Choose a library',
      body:
        'Each library is a separate folder of sessions, clips and reels. ' +
        'Switching does not move anything — the one you leave stays exactly ' +
        'as it is, and you can come back to it.',
    }
  }
  return {
    heading: 'Where should your videos live?',
    body:
      'SplitStep keeps your footage, clips and reels together in one folder. ' +
      'Video is big — a few hours fills tens of gigabytes — so most people ' +
      'put this on an external drive and leave their Mac’s disk alone.',
  }
}

/** The suggested path, pre-filled but never applied silently.
 *
 * ~/Movies is the macOS home for video: it is in Finder's sidebar, it is
 * covered by Time Machine, and it reads as obviously the user's. The path is
 * shown with the volume's free space beside it precisely so that someone on
 * a 256 GB MacBook sees the problem before 100 GB of footage arrives, which
 * is why there is a Continue button rather than a silent default.
 */
export function defaultLibraryPath(home: string): string {
  return `${home.replace(/\/+$/, '')}/Movies/SplitStep`
}

export function describeTarget(free: number | null, hasLibrary: boolean): string {
  const action = hasLibrary
    ? 'Open the library already in this folder'
    : 'Create a new library in this folder'
  if (free === null) return `${action}.`
  return `${action}. ${formatBytes(free)} free on this volume.`
}

/** Recency order, with unreachable libraries sunk to the bottom.
 *
 * Unreachable entries are kept rather than pruned -- an unplugged drive is
 * the single most common reason to be on this screen, and forgetting it is
 * the one thing the list must not do.
 */
export function sortEntries(entries: LibraryEntry[]): LibraryEntry[] {
  return [...entries].sort(
    (a, b) => Number(b.reachable) - Number(a.reachable),
  )
}
```

- [ ] **Step 4: Run the tests**

```bash
cd web && npx vitest run tests/launcher.test.ts 2>&1 | tail -5
```

Expected: `Tests  17 passed (17)`.

- [ ] **Step 5: Verify `formatBytes` is exported**

`describeTarget` imports it. If this fails, export it from `web/src/lib/reels.ts` rather than duplicating it — `web/src/lib/size.ts` was deleted in Phase 2 for being exactly that duplicate.

```bash
cd web && grep -n "export function formatBytes" src/lib/reels.ts
```

Expected: one match.

- [ ] **Step 6: Run the whole suite and the type check**

```bash
cd web && npx vitest run 2>&1 | tail -4 && npm run check 2>&1 | tail -2
```

Expected: `645 passed`, `0 ERRORS`.

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/launcher.ts web/tests/launcher.test.ts
git commit -m "feat(web): launcher logic — chooser state, copy and target description"
```

---

### Task 5: The front layer, built separately

**Files:**
- Create: `web/launcher.html`
- Create: `web/src/launcher/main.ts`
- Create: `web/src/launcher/Launcher.svelte`
- Create: `web/src/launcher/bridge.ts`
- Create: `web/vite.launcher.config.ts`
- Modify: `web/package.json` (one script)
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `web/src/lib/launcher.ts` (Task 4), `web/src/app.css`.
- Produces: `web/dist-launcher/` containing `launcher.html`; and `bridge.ts`'s `Bridge` interface, which Task 7 implements against real Tauri commands.

- [ ] **Step 1: Define the bridge the shell must satisfy**

Create `web/src/launcher/bridge.ts`:

```typescript
import type { LibraryEntry } from '../lib/launcher'

/** What the launcher needs from the shell.
 *
 * An interface rather than direct `invoke()` calls so the page renders in a
 * plain browser during development, where there is no Tauri runtime at all.
 * Task 7 supplies the real implementation.
 */
export type Bridge = {
  home(): Promise<string>
  known(): Promise<LibraryEntry[]>
  configured(): Promise<string | null>
  /** Native folder picker. Resolves null if the user cancels. */
  pickFolder(): Promise<string | null>
  /** Free bytes on the volume holding `path`, or null if it cannot be read. */
  freeSpace(path: string): Promise<number | null>
  /** Whether `path` already holds a library.db. */
  hasLibrary(path: string): Promise<boolean>
  /** Point of no return: writes config, spawns the sidecar, swaps the window. */
  open(path: string, create: boolean): Promise<void>
}

/** Browser fallback, for `npm run dev:launcher` with no Tauri around it.
 *
 * It is deliberately not a mock of a working app: pickFolder returns null and
 * open() throws, because a browser genuinely cannot do either, and a
 * fallback that pretended otherwise would hide the one thing worth testing
 * by hand here — the copy and the layout.
 */
export const browserBridge: Bridge = {
  home: async () => '/Users/you',
  known: async () => [],
  configured: async () => null,
  pickFolder: async () => null,
  freeSpace: async () => null,
  hasLibrary: async () => false,
  open: async () => {
    throw new Error('Opening a library needs the desktop app.')
  },
}

export function resolveBridge(): Bridge {
  const tauri = (globalThis as Record<string, unknown>).__SPLITSTEP_BRIDGE__
  return (tauri as Bridge) ?? browserBridge
}
```

- [ ] **Step 2: Write the page**

Create `web/src/launcher/Launcher.svelte`:

```svelte
<script lang="ts">
  import {
    chooserCopy,
    chooserState,
    defaultLibraryPath,
    describeTarget,
    sortEntries,
    type LibraryEntry,
  } from '../lib/launcher'
  import { resolveBridge } from './bridge'

  const bridge = resolveBridge()

  let known = $state<LibraryEntry[]>([])
  let configured = $state<string | null>(null)
  let target = $state('')
  let free = $state<number | null>(null)
  let targetHasLibrary = $state(false)
  let busy = $state(false)
  let error = $state<string | null>(null)
  let loaded = $state(false)

  const state = $derived(chooserState(known, configured))
  const copy = $derived(chooserCopy(state, configured))
  const entries = $derived(sortEntries(known))

  $effect(() => {
    void (async () => {
      const [home, list, current] = await Promise.all([
        bridge.home(), bridge.known(), bridge.configured(),
      ])
      known = list
      configured = current
      if (!target) await setTarget(defaultLibraryPath(home))
      loaded = true
    })()
  })

  async function setTarget(path: string) {
    target = path
    free = await bridge.freeSpace(path)
    targetHasLibrary = await bridge.hasLibrary(path)
  }

  async function choose() {
    const picked = await bridge.pickFolder()
    if (picked) await setTarget(picked)
  }

  async function go(path: string, create: boolean) {
    busy = true
    error = null
    try {
      await bridge.open(path, create)
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
      busy = false
    }
  }
</script>

<main class="mx-auto flex min-h-screen max-w-xl flex-col justify-center gap-6 p-8">
  <div class="flex flex-col gap-2">
    <h1 class="text-title text-fg">{copy.heading}</h1>
    <p class="text-body text-dim">{copy.body}</p>
  </div>

  {#if loaded && entries.length > 0}
    <ul class="flex flex-col gap-2">
      {#each entries as entry (entry.path)}
        <li>
          <button
            class="flex w-full flex-col items-start gap-1 rounded border border-line
                   bg-surface p-3 text-left hover:bg-surface-2
                   disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!entry.reachable || busy}
            onclick={() => go(entry.path, false)}
          >
            <span class="text-body text-fg">{entry.path.split('/').pop()}</span>
            <span class="text-caption font-data text-faint">{entry.path}</span>
            {#if !entry.reachable}
              <span class="text-caption text-danger">Not connected</span>
            {/if}
          </button>
        </li>
      {/each}
    </ul>
  {/if}

  <div class="flex flex-col gap-2 rounded border border-line bg-surface p-3">
    <span class="text-caption text-dim">New library</span>
    <span class="text-caption font-data text-faint break-all">{target}</span>
    <span class="text-caption text-dim">{describeTarget(free, targetHasLibrary)}</span>
    <div class="flex gap-2">
      <button
        class="rounded bg-accent px-3 py-1.5 text-body text-bg disabled:opacity-50"
        disabled={busy || !target}
        onclick={() => go(target, !targetHasLibrary)}
      >
        {targetHasLibrary ? 'Open' : 'Create'}
      </button>
      <button class="text-body text-accent" disabled={busy} onclick={choose}>
        Choose folder…
      </button>
    </div>
  </div>

  {#if error}
    <p class="text-caption text-danger">{error}</p>
  {/if}
</main>
```

- [ ] **Step 3: Write the entry point and HTML**

Create `web/src/launcher/main.ts`:

```typescript
import { mount } from 'svelte'
import '../app.css'
import Launcher from './Launcher.svelte'

mount(Launcher, { target: document.getElementById('app')! })
```

Create `web/launcher.html`:

```html
<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>SplitStep</title>
  </head>
  <body class="bg-bg">
    <div id="app"></div>
    <script type="module" src="/src/launcher/main.ts"></script>
  </body>
</html>
```

- [ ] **Step 4: Write the separate Vite config**

A second config, not a second entry in the existing one. `web/dist` ships inside the sidecar and must stay byte-identical to what it is today; adding an input to `vite.config.ts` would change its output.

Create `web/vite.launcher.config.ts`:

```typescript
import { svelte } from '@sveltejs/vite-plugin-svelte'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// Separate from vite.config.ts on purpose: web/dist is copied into the
// PyInstaller bundle and must keep producing exactly the files it does
// today, so the launcher gets its own output directory rather than an extra
// entry alongside index.html.
export default defineConfig({
  plugins: [svelte(), tailwindcss()],
  build: {
    outDir: 'dist-launcher',
    emptyOutDir: true,
    rollupOptions: { input: 'launcher.html' },
  },
  server: { port: 5174 },
})
```

- [ ] **Step 5: Add the scripts**

In `web/package.json`, add to `"scripts"`:

```json
    "build:launcher": "vite build --config vite.launcher.config.ts",
    "dev:launcher": "vite --config vite.launcher.config.ts"
```

- [ ] **Step 6: Ignore the output**

Append to `.gitignore`:

```
web/dist-launcher/
```

- [ ] **Step 7: Build it**

```bash
cd web && npm run build:launcher 2>&1 | tail -6
```

Expected: `✓ built in`, and a `dist-launcher/launcher.html` in the output list.

- [ ] **Step 8: Verify the main bundle is unchanged**

```bash
cd web && npm run build 2>&1 | grep -E "index-.*\.(js|css)"
```

Expected: the same hashed filenames as before this task. Different hashes mean the launcher leaked into the app bundle.

- [ ] **Step 9: Type-check and run the suite**

```bash
cd web && npm run check 2>&1 | tail -2 && npx vitest run 2>&1 | tail -3
```

Expected: `0 ERRORS`, `645 passed`.

- [ ] **Step 10: Commit**

```bash
git add web/launcher.html web/src/launcher web/vite.launcher.config.ts web/package.json .gitignore
git commit -m "feat(web): the front layer, built to its own bundle"
```

---

### Task 6: The shell exists and shows the chooser

**Files:**
- Create: `src-tauri/Cargo.toml`
- Create: `src-tauri/build.rs`
- Create: `src-tauri/tauri.conf.json`
- Create: `src-tauri/src/main.rs`
- Create: `src-tauri/src/sidecar.rs`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `web/dist-launcher/launcher.html` (Task 5), `packaging/dist/splitstep-server/` (Task 2).
- Produces: `sidecar::free_port() -> std::io::Result<u16>`, `sidecar::Sidecar::spawn(exe, library, create, port)`, `sidecar::wait_healthy(port, attempts, each) -> bool`. Task 7 calls all three from a command.

- [ ] **Step 1: Scaffold the crate**

Create `src-tauri/Cargo.toml`:

```toml
[package]
name = "splitstep-app"
version = "0.1.0"
edition = "2021"
rust-version = "1.77"

[build-dependencies]
tauri-build = { version = "2", features = [] }

[dependencies]
tauri = { version = "2", features = ["devtools"] }
tauri-plugin-dialog = "2"
tauri-plugin-single-instance = "2"
tauri-plugin-notification = "2"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
ureq = "2"
libc = "0.2"

[profile.release]
# The bundle is already ~2 GB of Python. The Rust half should not add to it.
opt-level = "s"
lto = true
strip = true
```

Create `src-tauri/build.rs`:

```rust
fn main() {
    tauri_build::build()
}
```

- [ ] **Step 2: Write the sidecar module**

Create `src-tauri/src/sidecar.rs`:

```rust
//! Spawning and health-checking the frozen Python server.

use std::io;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

/// Ask the OS for a port nobody is using.
///
/// Binding :0 and immediately dropping the listener leaves a microsecond
/// window where something else could take the port. That is tolerated rather
/// than solved with a stdout handshake, because the alternative means adding
/// shell-specific behaviour to a Python startup path that has none, and
/// `wait_healthy` failing is already the retry signal. A fixed port was
/// rejected outright: 8420 is where a developer's own `splitstep serve`
/// lives, and colliding with it on the one machine guaranteed to run both is
/// the worst possible default.
pub fn free_port() -> io::Result<u16> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

pub struct Sidecar {
    pub child: Child,
    pub port: u16,
}

impl Sidecar {
    pub fn spawn(
        exe: &Path,
        library: &Path,
        create: bool,
        port: u16,
    ) -> io::Result<Self> {
        let mut cmd = Command::new(exe);
        cmd.arg("--library").arg(library).arg("--port").arg(port.to_string());
        if create {
            // Only ever on the launch that creates a library. `serve
            // --create` refuses a path resolved from the env or the config
            // file, so it is always paired with an explicit --library here.
            cmd.arg("--create");
        }
        let child = cmd
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;
        Ok(Self { child, port })
    }

    /// SIGTERM, not kill. Jobs are idempotent and `reclaim_stale` covers a
    /// hard kill, so this is about closing sqlite cleanly rather than about
    /// correctness.
    pub fn terminate(&mut self) {
        unsafe { libc::kill(self.child.id() as i32, libc::SIGTERM) };
        let _ = self.child.wait();
    }
}

/// Poll `GET /api/config` until it answers.
///
/// That route is chosen because it already exists, is cheap, and needs no
/// library-specific state -- it answers as soon as the app is serving, which
/// is exactly the question being asked.
pub fn wait_healthy(port: u16, attempts: u32, each: Duration) -> bool {
    let url = format!("http://127.0.0.1:{port}/api/config");
    for _ in 0..attempts {
        let deadline = Instant::now() + each;
        while Instant::now() < deadline {
            if ureq::get(&url).timeout(Duration::from_millis(500)).call().is_ok() {
                return true;
            }
            std::thread::sleep(Duration::from_millis(250));
        }
    }
    false
}

/// Where the frozen server lives, bundle-first with a dev fallback -- the
/// same shape `splitstep/resources.py` uses on the Python side, for the same
/// reason: the dev loop must not need a bundle to exist.
pub fn server_exe(resource_dir: Option<&Path>) -> PathBuf {
    if let Some(dir) = resource_dir {
        let bundled = dir.join("splitstep-server").join("splitstep-server");
        if bundled.is_file() {
            return bundled;
        }
    }
    PathBuf::from("../packaging/dist/splitstep-server/splitstep-server")
}
```

- [ ] **Step 3: Write the failing Rust tests**

Append to `src-tauri/src/sidecar.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn free_port_returns_a_bindable_port() {
        let port = free_port().expect("a port");
        assert!(port > 1024, "should not hand back a privileged port");
        // The whole point is that it is free the instant we get it.
        TcpListener::bind(("127.0.0.1", port)).expect("port should be bindable");
    }

    #[test]
    fn free_port_does_not_repeat_itself_immediately() {
        let a = free_port().unwrap();
        let b = free_port().unwrap();
        assert_ne!(a, b, "two calls in a row must not collide");
    }

    #[test]
    fn wait_healthy_gives_up_rather_than_hanging() {
        let port = free_port().unwrap();
        let start = Instant::now();
        assert!(!wait_healthy(port, 1, Duration::from_millis(600)));
        assert!(start.elapsed() < Duration::from_secs(3), "must not hang");
    }

    #[test]
    fn server_exe_falls_back_when_there_is_no_bundle() {
        let path = server_exe(None);
        assert!(path.to_string_lossy().contains("packaging/dist"));
    }
}
```

- [ ] **Step 4: Write main.rs**

Create `src-tauri/src/main.rs`:

```rust
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .run(tauri::generate_context!())
        .expect("error while running SplitStep");
}
```

- [ ] **Step 5: Write the Tauri config**

Create `src-tauri/tauri.conf.json`:

```json
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "SplitStep",
  "version": "0.1.0",
  "identifier": "com.stevenkhaw.splitstep",
  "build": {
    "frontendDist": "../web/dist-launcher",
    "devUrl": "http://localhost:5174",
    "beforeDevCommand": "cd ../web && npm run dev:launcher",
    "beforeBuildCommand": "cd ../web && npm run build && npm run build:launcher"
  },
  "app": {
    "withGlobalTauri": true,
    "windows": [
      {
        "title": "SplitStep",
        "url": "launcher.html",
        "width": 900,
        "height": 640,
        "minWidth": 720,
        "minHeight": 520,
        "resizable": true,
        "backgroundColor": "#0b0b0e"
      }
    ],
    "security": { "csp": null }
  },
  "bundle": {
    "active": true,
    "targets": ["dmg"],
    "icon": ["icons/icon.icns"],
    "resources": ["../packaging/dist/splitstep-server"],
    "macOS": { "minimumSystemVersion": "12.0" }
  }
}
```

`withGlobalTauri` is what lets Task 7 inject a bridge built on `window.__TAURI__.core.invoke` without adding `@tauri-apps/api` to `web/package.json` — the launcher stays a plain Vite page with no Tauri build-time dependency, which is also what keeps `browserBridge` honest.

- [ ] **Step 6: Ignore Rust build output**

Append to `.gitignore`:

```
src-tauri/target/
src-tauri/gen/
```

- [ ] **Step 7: Run the Rust tests**

```bash
cd src-tauri && cargo test 2>&1 | tail -15
```

Expected: `test result: ok. 4 passed`. First run compiles the whole Tauri tree and takes several minutes.

- [ ] **Step 8: Run the shell in dev and see the chooser**

```bash
cd web && npm run build:launcher && cd ../src-tauri && cargo run 2>&1 | tail -20
```

Expected: a window titled SplitStep showing "Where should your videos live?". Nothing works yet — `browserBridge` is in force because Task 7 has not injected the real one.

- [ ] **Step 9: Commit**

```bash
git add src-tauri .gitignore
git commit -m "feat(shell): tauri scaffold, port selection and sidecar spawn"
```

---

### Task 7: The chooser actually opens a library

**Files:**
- Modify: `src-tauri/src/main.rs`
- Create: `src-tauri/src/commands.rs`
- Create: `src-tauri/src/state.rs`
- Create: `src-tauri/src/bridge.js`

**Interfaces:**
- Consumes: `sidecar::free_port`, `Sidecar::spawn`, `wait_healthy`, `server_exe` (Task 6); the `Bridge` shape from `web/src/launcher/bridge.ts` (Task 5).
- Produces: seven `#[tauri::command]`s named exactly `home`, `known`, `configured`, `pick_folder`, `free_space`, `has_library`, `open_library`. plus `commands::autoboot(&AppHandle) -> bool`. Task 8 reads
  `AppState.sidecar`; Task 9 calls `back_to_chooser`.

- [ ] **Step 1: Hold the running sidecar**

Create `src-tauri/src/state.rs`:

```rust
use std::sync::Mutex;

use crate::sidecar::Sidecar;

/// The one running sidecar, or none while the chooser is up.
///
/// A Mutex rather than a channel because every consumer (quit, the health
/// poller, Change library) only ever needs "is there one, and what port" --
/// there is no stream of events to carry.
#[derive(Default)]
pub struct AppState {
    pub sidecar: Mutex<Option<Sidecar>>,
}
```

- [ ] **Step 2: Write the commands**

Create `src-tauri/src/commands.rs`:

```rust
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::Serialize;
use tauri::{Manager, State};
use tauri_plugin_dialog::DialogExt;

use crate::sidecar::{free_port, server_exe, wait_healthy, Sidecar};
use crate::state::AppState;

#[derive(Serialize)]
pub struct LibraryEntry {
    pub path: String,
    pub reachable: bool,
    #[serde(rename = "hasLibrary")]
    pub has_library: bool,
}

fn config_path() -> PathBuf {
    // Must agree byte-for-byte with appconfig.config_path(), which uses
    // platformdirs' user_config_dir("splitstep"). Hardcoded here rather than
    // shelled out to Python, because the chooser runs before any Python
    // process exists -- that is the whole reason this layer is native.
    let home = std::env::var("HOME").unwrap_or_default();
    PathBuf::from(home)
        .join("Library/Application Support/splitstep/config.json")
}

fn read_config() -> serde_json::Value {
    std::fs::read_to_string(config_path())
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_else(|| serde_json::json!({}))
}

fn write_config(cfg: &serde_json::Value) -> std::io::Result<()> {
    let path = config_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    // Temp-file + rename, matching appconfig.save_config's atomicity. A
    // torn config is read by the chooser itself, so a half-written file
    // would break the screen that repairs it.
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, serde_json::to_string_pretty(cfg)?)?;
    std::fs::rename(tmp, path)
}

#[tauri::command]
pub fn home() -> String {
    std::env::var("HOME").unwrap_or_else(|_| "/".into())
}

#[tauri::command]
pub fn configured() -> Option<String> {
    read_config()
        .get("library")
        .and_then(|v| v.as_str())
        .map(str::to_string)
}

#[tauri::command]
pub fn known() -> Vec<LibraryEntry> {
    let cfg = read_config();
    let paths = cfg
        .get("libraries")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    paths
        .iter()
        .filter_map(|v| v.as_str())
        .map(|path| LibraryEntry {
            path: path.to_string(),
            // "Reachable" is the parent existing, not the library.db: a
            // mounted drive whose library was deleted is a different problem
            // from an unplugged drive, and only the second one is fixed by
            // plugging something in.
            reachable: Path::new(path).is_dir(),
            has_library: Path::new(path).join("library.db").is_file(),
        })
        .collect()
}

#[tauri::command]
pub fn has_library(path: String) -> bool {
    Path::new(&path).join("library.db").is_file()
}

#[tauri::command]
pub fn free_space(path: String) -> Option<u64> {
    // statvfs walks up to the mount point on its own, so an as-yet
    // uncreated ~/Movies/SplitStep still reports the right volume.
    let mut probe = PathBuf::from(&path);
    while !probe.exists() {
        match probe.parent() {
            Some(parent) => probe = parent.to_path_buf(),
            None => return None,
        }
    }
    let c_path = std::ffi::CString::new(probe.to_string_lossy().as_bytes()).ok()?;
    let mut stat: libc::statvfs = unsafe { std::mem::zeroed() };
    if unsafe { libc::statvfs(c_path.as_ptr(), &mut stat) } != 0 {
        return None;
    }
    Some(stat.f_bavail as u64 * stat.f_frsize as u64)
}

#[tauri::command]
pub async fn pick_folder(app: tauri::AppHandle) -> Option<String> {
    let (tx, rx) = std::sync::mpsc::channel();
    app.dialog().file().pick_folder(move |picked| {
        let _ = tx.send(picked);
    });
    rx.recv()
        .ok()
        .flatten()
        .and_then(|p| p.into_path().ok())
        .map(|p| p.to_string_lossy().to_string())
}

#[tauri::command]
pub fn open_library(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    path: String,
    create: bool,
) -> Result<(), String> {
    let library = PathBuf::from(&path);
    if create {
        std::fs::create_dir_all(&library).map_err(|e| e.to_string())?;
    } else if !library.is_dir() {
        return Err(format!("{path} is not available."));
    }

    let resource_dir = app.path().resource_dir().ok();
    let exe = server_exe(resource_dir.as_deref());

    // Three attempts, each on a freshly assigned port: the only realistic
    // failure is losing the free-port race, and a new port is the fix.
    let mut last_error = String::new();
    for _ in 0..3 {
        let port = free_port().map_err(|e| e.to_string())?;
        match Sidecar::spawn(&exe, &library, create, port) {
            Ok(mut child) => {
                if wait_healthy(port, 1, Duration::from_secs(20)) {
                    let mut cfg = read_config();
                    cfg["library"] = serde_json::json!(path);
                    // Friend mode is written on the launch that creates a
                    // library and never after: a dev who later flips the
                    // Settings toggle must not have it flipped back.
                    if create {
                        cfg["mode"] = serde_json::json!("friend");
                    }
                    let mut list: Vec<String> = known()
                        .into_iter()
                        .map(|e| e.path)
                        .filter(|p| p != &path)
                        .collect();
                    list.insert(0, path.clone());
                    cfg["libraries"] = serde_json::json!(list);
                    write_config(&cfg).map_err(|e| e.to_string())?;

                    let url = format!("http://127.0.0.1:{port}/");
                    let window = app
                        .get_webview_window("main")
                        .ok_or("no main window")?;
                    window
                        .navigate(url.parse().map_err(|_| "bad url")?)
                        .map_err(|e| e.to_string())?;
                    *state.sidecar.lock().unwrap() = Some(child);
                    return Ok(());
                }
                let stderr = child
                    .child
                    .stderr
                    .take()
                    .map(|mut s| {
                        use std::io::Read;
                        let mut buf = String::new();
                        let _ = s.read_to_string(&mut buf);
                        buf
                    })
                    .unwrap_or_default();
                child.terminate();
                last_error = stderr.lines().rev().take(3).collect::<Vec<_>>().join(" ");
            }
            Err(e) => last_error = e.to_string(),
        }
    }
    Err(if last_error.is_empty() {
        "SplitStep could not start its server.".into()
    } else {
        format!("SplitStep could not start its server. {last_error}")
    })
}
```

- [ ] **Step 3: Write the bridge injection**

Create `src-tauri/src/bridge.js`:

```javascript
// Injected into every page before it loads. It defines the object
// web/src/launcher/bridge.ts looks for, so the launcher never imports a
// Tauri package and still renders in a plain browser via browserBridge.
(function () {
  const invoke = window.__TAURI__.core.invoke
  window.__SPLITSTEP_BRIDGE__ = {
    home: () => invoke('home'),
    known: () => invoke('known'),
    configured: () => invoke('configured'),
    pickFolder: () => invoke('pick_folder'),
    freeSpace: (path) => invoke('free_space', { path }),
    hasLibrary: (path) => invoke('has_library', { path }),
    open: (path, create) => invoke('open_library', { path, create }),
  }
})()
```

- [ ] **Step 4: Wire it all up in main.rs**

Replace `src-tauri/src/main.rs` with:

```rust
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;
mod sidecar;
mod state;

use tauri::Manager;

use crate::state::AppState;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .manage(AppState::default())
        .setup(|app| {
            let window = app.get_webview_window("main").expect("main window");
            // Injected on every navigation, including the one to the Python
            // server, so a page served by the app can call back into the
            // shell later (Task 9's Change library).
            window.eval(include_str!("bridge.js"))?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::home,
            commands::known,
            commands::configured,
            commands::pick_folder,
            commands::free_space,
            commands::has_library,
            commands::open_library,
        ])
        .run(tauri::generate_context!())
        .expect("error while running SplitStep");
}
```

- [ ] **Step 5: Skip the chooser when a library is already configured**

The chooser is for a first run, a missing drive, or a deliberate switch — not
a toll booth on every launch. Without this the window always lands on it.

In `src-tauri/tauri.conf.json`, add `"visible": false` to the window object.
The window starts hidden and is shown either after the auto-boot navigates
(no chooser flash) or immediately when the chooser is needed.

Add to `src-tauri/src/commands.rs`:

```rust
/// Boot straight into a configured, reachable library.
///
/// Returns false when the chooser must be shown instead: nothing configured,
/// or the configured path is not a directory right now -- which is the
/// unplugged-drive case, and the one the chooser's "not connected" banner
/// exists for. A configured library whose server fails to start is NOT
/// handled here; that falls through to the chooser too, where the error is
/// visible and fixable.
pub fn autoboot(app: &tauri::AppHandle) -> bool {
    let Some(path) = configured() else { return false };
    if !Path::new(&path).is_dir() {
        return false;
    }
    let state = app.state::<AppState>();
    open_library(app.clone(), state, path, false).is_ok()
}
```

In `src-tauri/src/main.rs`, replace the body of `.setup(...)` with:

```rust
        .setup(|app| {
            let window = app.get_webview_window("main").expect("main window");
            window.eval(include_str!("bridge.js"))?;
            lifecycle::watch(app.handle().clone());

            // Done on a thread: autoboot blocks for up to 20 seconds waiting
            // on the health check, and blocking setup() means a beachball
            // instead of a window.
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                commands::autoboot(&handle);
                if let Some(window) = handle.get_webview_window("main") {
                    let _ = window.show();
                }
            });
            Ok(())
        })
```

`autoboot` navigates before the window is shown when it succeeds, so a
returning user never sees the chooser; when it returns false the window is
shown as-is, on the chooser, with `chooserState` resolving to `not-connected`
if a library was configured and `first-run` if none was.

- [ ] **Step 6: Build and check it compiles**

```bash
cd src-tauri && cargo build 2>&1 | tail -20
```

Expected: `Finished`. Compile errors here are almost always Tauri v2 API drift — check the signature against the installed crate's docs rather than guessing.

- [ ] **Step 7: Run the Rust tests**

```bash
cd src-tauri && cargo test 2>&1 | tail -6
```

Expected: `4 passed`.

- [ ] **Step 8: Verify end to end by hand**

```bash
cd packaging && ls dist/splitstep-server/splitstep-server && cd ../src-tauri && cargo run
```

Then in the window: click **Choose folder…**, pick a new empty folder in `/tmp`, click **Create**. Expected: after ~15 seconds the window becomes the SplitStep app showing an empty session list. Quit, relaunch, and confirm it goes straight into the app without the chooser.

- [ ] **Step 9: Verify the config was written correctly**

```bash
cat ~/Library/Application\ Support/splitstep/config.json
```

Expected: `library` set to the folder just picked, `libraries` an array containing it, `mode` set to `friend`. Restore your own config afterwards if this overwrote it.

- [ ] **Step 10: Commit**

```bash
git add src-tauri
git commit -m "feat(shell): the chooser opens a library and swaps the window"
```

---

### Task 8: Lifecycle — one instance, clean quit, crash dialog, no App Nap, notifications

**Files:**
- Create: `src-tauri/src/lifecycle.rs`
- Modify: `src-tauri/src/main.rs`
- Modify: `src-tauri/Cargo.toml`

**Interfaces:**
- Consumes: `AppState.sidecar` (Task 7).
- Produces: `lifecycle::watch(app: tauri::AppHandle)`, started once from `setup`.

- [ ] **Step 1: Add the single-instance plugin**

`tauri-plugin-single-instance` is already in `Cargo.toml` from Task 6. Two workers on one `library.db` is the enqueue race `jobs/handlers.py` names in a TODO; this plugin is the first line of defence and Phase 1's fix is the second.

- [ ] **Step 2: Write the watcher**

Create `src-tauri/src/lifecycle.rs`:

```rust
//! One poller answering three questions that share an answer.

use std::process::{Child, Command, Stdio};
use std::time::Duration;

use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_notification::NotificationExt;

use crate::state::AppState;

/// Poll /api/jobs and act on it.
///
/// Power assertion and the finished-detect notification need the same
/// answer to the same question -- are any jobs pending -- so they share one
/// poller rather than each running their own. Five seconds is chosen against
/// a fifteen-minute detect: fast enough that the notification feels
/// immediate, slow enough to be free.
pub fn watch(app: AppHandle) {
    std::thread::spawn(move || {
        let mut caffeinate: Option<Child> = None;
        let mut was_detecting = false;
        loop {
            std::thread::sleep(Duration::from_secs(5));
            let state = app.state::<AppState>();
            let port = {
                let guard = state.sidecar.lock().unwrap();
                match guard.as_ref() {
                    Some(s) => s.port,
                    // No sidecar means the chooser is up. Nothing to poll,
                    // and no assertion to hold.
                    None => {
                        if let Some(mut child) = caffeinate.take() {
                            let _ = child.kill();
                        }
                        continue;
                    }
                }
            };

            if !alive(&app) {
                offer_relaunch(&app);
                return;
            }

            let body = match ureq::get(&format!("http://127.0.0.1:{port}/api/jobs"))
                .timeout(Duration::from_secs(3))
                .call()
                .and_then(|r| r.into_string().map_err(Into::into))
            {
                Ok(text) => text,
                Err(_) => continue,
            };

            let pending = body.contains("\"queued\"") || body.contains("\"running\"");
            let detecting = body.contains("\"detect\"") && pending;

            // caffeinate -s, not an IOKit assertion: it is a system binary
            // present on every Mac, it dies with us if we crash, and it
            // needs no objc FFI in a shell that is deliberately thin.
            if pending && caffeinate.is_none() {
                caffeinate = Command::new("/usr/bin/caffeinate")
                    .arg("-s")
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn()
                    .ok();
            } else if !pending {
                if let Some(mut child) = caffeinate.take() {
                    let _ = child.kill();
                }
            }

            if was_detecting && !detecting {
                let _ = app
                    .notification()
                    .builder()
                    .title("Detection finished")
                    .body("Your rallies are ready to review.")
                    .show();
            }
            was_detecting = detecting;
        }
    });
}

fn alive(app: &AppHandle) -> bool {
    let state = app.state::<AppState>();
    let mut guard = state.sidecar.lock().unwrap();
    match guard.as_mut() {
        // try_wait returning Ok(Some(_)) means it exited.
        Some(sidecar) => !matches!(sidecar.child.try_wait(), Ok(Some(_))),
        None => true,
    }
}

/// Without this a crashed Python process leaves a blank window and no
/// explanation, which reads as "the app is broken" with nowhere to go.
fn offer_relaunch(app: &AppHandle) {
    let handle = app.clone();
    app.dialog()
        .message("SplitStep's server stopped unexpectedly.")
        .title("SplitStep")
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Relaunch".into(),
            "Quit".into(),
        ))
        .show(move |relaunch| {
            if relaunch {
                handle.restart();
            } else {
                handle.exit(0);
            }
        });
}

/// SIGTERM on quit. Jobs are idempotent and `reclaim_stale` covers a hard
/// kill, so this is about closing sqlite cleanly, not about correctness.
pub fn shutdown(app: &AppHandle) {
    let state = app.state::<AppState>();
    if let Some(mut sidecar) = state.sidecar.lock().unwrap().take() {
        sidecar.terminate();
    }
}
```

- [ ] **Step 3: Wire it into main.rs**

Replace the `tauri::Builder::default()` chain in `src-tauri/src/main.rs` with:

```rust
fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            // Second launch: focus the window we already have rather than
            // starting a second worker against one library.db.
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .manage(AppState::default())
        .setup(|app| {
            let window = app.get_webview_window("main").expect("main window");
            window.eval(include_str!("bridge.js"))?;
            lifecycle::watch(app.handle().clone());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::home,
            commands::known,
            commands::configured,
            commands::pick_folder,
            commands::free_space,
            commands::has_library,
            commands::open_library,
        ])
        .build(tauri::generate_context!())
        .expect("error while building SplitStep")
        .run(|app, event| {
            if let tauri::RunEvent::ExitRequested { .. } = event {
                lifecycle::shutdown(app);
            }
        });
}
```

Add `mod lifecycle;` beside the other `mod` lines.

- [ ] **Step 4: Build**

```bash
cd src-tauri && cargo build 2>&1 | tail -15
```

Expected: `Finished`.

- [ ] **Step 5: Verify single instance**

```bash
cd src-tauri && cargo run &
sleep 25 && cargo run 2>&1 | tail -3
```

Expected: the second invocation exits immediately; the first window takes focus. Only one window on screen.

- [ ] **Step 6: Verify graceful quit leaves no orphan**

Quit the app from the menu, then:

```bash
pgrep -fl splitstep-server || echo "no orphan"
```

Expected: `no orphan`.

- [ ] **Step 7: Verify the relaunch dialog**

With the app running and past the chooser:

```bash
pkill -f splitstep-server
```

Expected: within ~5 seconds a dialog reads "SplitStep's server stopped unexpectedly." with Relaunch and Quit.

- [ ] **Step 8: Commit**

```bash
git add src-tauri
git commit -m "feat(shell): single instance, clean quit, crash dialog, power assertion, notifications"
```

---

### Task 9: Change library from inside the app

**Files:**
- Modify: `src-tauri/src/commands.rs`
- Modify: `src-tauri/src/main.rs`
- Modify: `src-tauri/src/bridge.js`
- Modify: `web/src/routes/Library.svelte`
- Create: `web/src/lib/shell.ts`
- Test: `web/tests/shell.test.ts`

**Interfaces:**
- Consumes: `AppState.sidecar`, the Settings popover already in `Library.svelte` (Phase 2).
- Produces: `shell.inShell(): boolean` and `shell.changeLibrary(): Promise<void>`; Rust command `back_to_chooser`.

- [ ] **Step 1: Write the failing tests**

Create `web/tests/shell.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from 'vitest'
import { changeLibrary, inShell } from '../src/lib/shell'

const g = globalThis as Record<string, unknown>

afterEach(() => {
  delete g.__SPLITSTEP_BRIDGE__
})

describe('inShell', () => {
  it('is false in a plain browser', () => {
    expect(inShell()).toBe(false)
  })

  it('is true when the shell injected its bridge', () => {
    g.__SPLITSTEP_BRIDGE__ = { backToChooser: async () => {} }
    expect(inShell()).toBe(true)
  })
})

describe('changeLibrary', () => {
  it('asks the shell to go back to the chooser', async () => {
    const backToChooser = vi.fn().mockResolvedValue(undefined)
    g.__SPLITSTEP_BRIDGE__ = { backToChooser }
    await changeLibrary()
    expect(backToChooser).toHaveBeenCalledOnce()
  })

  it('throws a sentence, not undefined, outside the shell', async () => {
    // The browser tier has no shell to ask, and the button is hidden there --
    // but a stale tab could still reach this, and it must say something.
    await expect(changeLibrary()).rejects.toThrow(/desktop app/i)
  })
})
```

- [ ] **Step 2: Run and watch it fail**

```bash
cd web && npx vitest run tests/shell.test.ts 2>&1 | tail -4
```

Expected: FAIL, `Failed to resolve import "../src/lib/shell"`.

- [ ] **Step 3: Implement the client half**

Create `web/src/lib/shell.ts`:

```typescript
/** Whether the page is running inside the Tauri shell.
 *
 * The bridge object is injected on every navigation, including the one to
 * the Python server, so the app tier can ask the shell for the few things
 * only it can do. In the browser tier it is simply absent, and every
 * shell-only affordance hides itself rather than failing on click.
 */
type ShellBridge = { backToChooser(): Promise<void> }

function bridge(): ShellBridge | null {
  const found = (globalThis as Record<string, unknown>).__SPLITSTEP_BRIDGE__
  return (found as ShellBridge) ?? null
}

export function inShell(): boolean {
  return bridge() !== null
}

export async function changeLibrary(): Promise<void> {
  const found = bridge()
  if (!found) throw new Error('Changing library needs the desktop app.')
  await found.backToChooser()
}
```

- [ ] **Step 4: Run the tests**

```bash
cd web && npx vitest run tests/shell.test.ts 2>&1 | tail -4
```

Expected: `4 passed`.

- [ ] **Step 5: Add the Rust command**

Append to `src-tauri/src/commands.rs`:

```rust
/// Tear the sidecar down and return to the chooser.
///
/// Re-point, never move: the library being left is untouched on disk, and it
/// is already in `libraries`, so coming back to it is one click. That is the
/// whole contract of changing location.
#[tauri::command]
pub fn back_to_chooser(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<(), String> {
    if let Some(mut sidecar) = state.sidecar.lock().unwrap().take() {
        sidecar.terminate();
    }
    let window = app.get_webview_window("main").ok_or("no main window")?;
    // In a bundle the launcher is a file: URL under the asset protocol; in
    // dev it is the Vite server. `tauri://localhost/launcher.html` resolves
    // to the right one either way.
    window
        .navigate("tauri://localhost/launcher.html".parse().map_err(|_| "bad url")?)
        .map_err(|e| e.to_string())
}
```

Register it in `main.rs`'s `generate_handler!` list, after `commands::open_library`.

- [ ] **Step 6: Expose it on the bridge**

In `src-tauri/src/bridge.js`, add inside the object literal:

```javascript
    backToChooser: () => invoke('back_to_chooser'),
```

- [ ] **Step 7: Add the Settings entry**

In `web/src/routes/Library.svelte`, add to the imports:

```typescript
  import { changeLibrary, inShell } from '../lib/shell'
```

And inside the Settings popover, after the existing friend/dev toggle:

```svelte
    {#if inShell()}
      <button
        class="text-body text-accent text-left"
        onclick={() => changeLibrary()}
      >
        Change library…
      </button>
    {/if}
```

- [ ] **Step 8: Verify**

```bash
cd web && npm run check 2>&1 | tail -2 && npx vitest run 2>&1 | tail -3 && cd ../src-tauri && cargo build 2>&1 | tail -3
```

Expected: `0 ERRORS`, `649 passed`, `Finished`.

- [ ] **Step 9: Verify by hand**

Run the app, get into a library, open Settings, click **Change library…**. Expected: the chooser returns, listing the library just left plus any others, and picking it again goes straight back in.

- [ ] **Step 10: Commit**

```bash
git add web src-tauri
git commit -m "feat(shell): change library from Settings"
```

---

### Task 10: Icon, `.dmg`, credits, README

**Files:**
- Create: `packaging/make_icon.py`
- Create: `src-tauri/icons/icon.icns`
- Create: `packaging/build_app.sh`
- Create: `docs/INSTALL.md`
- Create: `web/src/components/Credits.svelte`
- Modify: `web/src/routes/Library.svelte`

**Interfaces:**
- Consumes: everything above.
- Produces: `src-tauri/target/release/bundle/dmg/SplitStep_0.1.0_aarch64.dmg`.

- [ ] **Step 1: Write the icon generator**

Create `packaging/make_icon.py`:

```python
"""Generate the app icon from the design tokens.

Pillow is already a dependency (media/numbered.py renders the overlay PNG
with it), so this adds nothing to the bundle. The mark is two offset bars --
a split, and a step -- in the app's accent over its base ground, chosen
because it stays legible at 16px where anything representational turns to
mud.
"""

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

BG = (11, 11, 14)
ACCENT = (167, 139, 250)
DIM = (110, 100, 140)
SIZES = (16, 32, 64, 128, 256, 512, 1024)


def render(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (*BG, 255))
    draw = ImageDraw.Draw(img)
    unit = size / 16
    radius = unit * 2.5
    draw.rounded_rectangle(
        [(0, 0), (size - 1, size - 1)], radius=radius, fill=(*BG, 255)
    )
    # Two bars, offset vertically: the lower-left one dim, the upper-right
    # one accent. The gap between them is the "split".
    bar_w, bar_h = unit * 4, unit * 7
    draw.rounded_rectangle(
        [(unit * 3, unit * 6), (unit * 3 + bar_w, unit * 6 + bar_h)],
        radius=unit, fill=(*DIM, 255),
    )
    draw.rounded_rectangle(
        [(unit * 9, unit * 3), (unit * 9 + bar_w, unit * 3 + bar_h)],
        radius=unit, fill=(*ACCENT, 255),
    )
    return img


def main() -> int:
    out = Path(__file__).parent.parent / "src-tauri" / "icons"
    out.mkdir(parents=True, exist_ok=True)
    iconset = out / "icon.iconset"
    iconset.mkdir(exist_ok=True)
    for size in SIZES:
        render(size).save(iconset / f"icon_{size}x{size}.png")
        # iconutil wants @2x variants; render them natively rather than
        # upscaling, so the 16px mark is designed at 32px too.
        if size <= 512:
            render(size * 2).save(iconset / f"icon_{size}x{size}@2x.png")
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(out / "icon.icns")],
        check=True,
    )
    render(1024).save(out / "icon.png")
    print(f"wrote {out / 'icon.icns'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Generate the icon**

```bash
~/miniconda3/envs/splitstep/bin/python packaging/make_icon.py && ls -la src-tauri/icons/
```

Expected: `icon.icns` present and non-trivial in size.

- [ ] **Step 3: Look at it**

```bash
open src-tauri/icons/icon.png
```

Expected: legible mark, no white box, correct dark ground. If it reads as mud at small size, simplify further rather than adding detail.

- [ ] **Step 4: Write the credits component**

Create `web/src/components/Credits.svelte`:

```svelte
<!-- Two licence obligations ship with the app: Ultralytics is AGPL-3.0 and
     the evermeet.cx ffmpeg builds are GPL. The public repository satisfies
     the source obligation; this names them where a user can find them. -->
<div class="flex flex-col gap-1">
  <span class="text-caption text-dim">Open source</span>
  <span class="text-caption text-faint">
    Detection by Ultralytics YOLO11 (AGPL-3.0). Video by FFmpeg (GPL).
    Source: github.com/stevenkhaw/SplitStep
  </span>
</div>
```

Render it inside the Settings popover in `web/src/routes/Library.svelte`, below the Change library entry.

- [ ] **Step 5: Write the build script**

Create `packaging/build_app.sh`:

```bash
#!/bin/bash
# One command from a clean checkout to a .dmg. Every step is here rather
# than in a README because a build nobody can reproduce is a build that
# breaks silently.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=~/miniconda3/envs/splitstep/bin

echo "==> assets"
./packaging/fetch_assets.sh

echo "==> web"
cd web && npm run build && npm run build:launcher && cd ..

echo "==> icon"
"$PY/python" packaging/make_icon.py

echo "==> freeze"
cd packaging && "$PY/pyinstaller" --noconfirm --distpath dist --workpath build splitstep.spec && cd ..

echo "==> app"
cd src-tauri && cargo tauri build --target aarch64-apple-darwin

echo "==> done"
ls -lh target/aarch64-apple-darwin/release/bundle/dmg/*.dmg
```

- [ ] **Step 6: Install the Tauri CLI**

```bash
cargo install tauri-cli --version "^2" --locked 2>&1 | tail -3
```

Expected: `Installed package tauri-cli`. This takes several minutes.

- [ ] **Step 7: Build the app**

```bash
chmod +x packaging/build_app.sh && ./packaging/build_app.sh 2>&1 | tail -25
```

Expected: a `.dmg` path printed, roughly 1.5–2 GB.

- [ ] **Step 8: Write the install instructions**

Create `docs/INSTALL.md`:

```markdown
# Installing SplitStep

SplitStep is not signed by Apple, so macOS blocks it the first time. This is
one extra step, once.

1. Copy `SplitStep.dmg` to your Mac and double-click it.
2. Drag **SplitStep** into your Applications folder.
3. **Right-click** SplitStep in Applications and choose **Open** — do not
   double-click it the first time.
4. macOS says it cannot verify the developer. Click **Open**.
5. If there is no Open button, go to **System Settings → Privacy & Security**,
   scroll down, and click **Open Anyway** next to SplitStep. Then try step 3
   again.

After that first launch it opens normally forever.

## First run

SplitStep asks where to keep your videos. Video is big — a few hours of play
fills tens of gigabytes — so if you have an external drive, plug it in and
choose a folder there. Otherwise the suggested folder in Movies is fine.

If you later open SplitStep without that drive plugged in, it says so and
waits. Plug it in and click Retry.

## Requirements

Apple Silicon Mac (M1 or later), macOS 12 or later. Intel Macs are not
supported.
```

- [ ] **Step 9: Verify the .dmg on a clean account**

The real test. Create a second macOS user account, log into it, and run the
checklist:

```
[ ] copy the .dmg over, double-click, drag to Applications
[ ] right-click -> Open, get through Gatekeeper
[ ] chooser appears, explains why the folder matters
[ ] free space shown, path pre-filled at ~/Movies/SplitStep
[ ] Choose folder -> pick an external drive folder -> Create
[ ] app opens, first-run video drop appears
[ ] drop a video, wizard appears, set rotation and quad
[ ] detection runs; a notification fires when it finishes
[ ] review a rally, star it
[ ] export a clip, watch it in the Clips panel
[ ] build a reel and render it
[ ] Settings -> Change library -> chooser returns with both libraries
[ ] quit; no orphan splitstep-server process
[ ] unplug the drive, relaunch: "not connected", Retry works
```

- [ ] **Step 10: Commit**

```bash
git add packaging docs/INSTALL.md src-tauri/icons web/src/components/Credits.svelte web/src/routes/Library.svelte
git commit -m "build: icon, dmg, credits and install instructions"
```

---

## Done when

- `packaging/build_app.sh` produces a `.dmg` from a clean checkout.
- The clean-account checklist in Task 10 Step 9 passes end to end.
- 845 pytest, 649 vitest, `svelte-check` 0 errors, `cargo test` 4 passed, ruff clean.
- `HANDOFF.md` records the phase as shipped and what the checklist found.
