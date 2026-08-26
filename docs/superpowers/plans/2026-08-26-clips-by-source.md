# Clips Grouped by Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clips move from `sessions/<date>/clips/NN-START-END.mp4` to `sessions/<date>/clips/NN/START-END.mp4` — one folder per source video — with a one-time idempotent on-disk reconciliation and no change to the span-derived-path invariant.

**Architecture:** One pure function (`clip_relpath`) emits the new shape; its inverse (`parse_clip_name`) accepts both shapes so legacy strays stay self-identifying; a startup sweep moves old files and rewrites `rallies.clip_path` rows. Spec: `docs/superpowers/specs/2026-08-26-clips-by-source-design.md`.

**Branch:** `phase1-server-friend-readiness`, checkout `/Users/stevenkhaw/Documents/GitHub/SplitStep-phase1-test`. Master is NOT touched.

## Global Constraints

- Interpreter: `~/miniconda3/envs/splitstep/bin/python -m pytest …` — the `-m` form is REQUIRED in this worktree. Full suite exactly once before each commit, ONE FOREGROUND Bash call, timeout 600000ms; no backgrounding/polling/watchers.
- `~/miniconda3/envs/splitstep/bin/ruff check splitstep tests` clean before every commit (broader defaults: I001 import sorting, BLE001, PLW1510; line length 100).
- pytest `filterwarnings = ["error"]`.
- Comments explain why, not what; never strip existing rationale comments — `clip_relpath`/`parse_clip_name`/`find_orphan_clips` carry load-bearing ones; extend, don't delete.
- Commit style: lowercase `type(scope): imperative summary`.
- Baseline on the branch: 771 passed, ruff clean, at a021a2d.
- **Ordering guarantee to preserve:** the reconcile sweep must run before anything calls `plan_export` in the same process — a claimed flat clip that hasn't moved yet would read as "not cut" and re-encode. `serve` runs it at startup before the worker starts; each `clips` CLI command runs it before planning.

---

### Task 1: `clip_relpath` nested + `parse_clip_name` dual-shape

**Files:**
- Modify: `splitstep/media/clips.py`
- Test: `tests/test_clips.py` (or the file currently holding clip-name tests — grep `parse_clip_name` in `tests/` and extend where those live)

**Interfaces:**
- Produces: `clip_relpath(source_idx, start_ms, end_ms) -> str` now returns `f"{source_idx:02d}/{start_ms}-{end_ms}.mp4"`. `parse_clip_name(name: str) -> tuple[int, int, int] | None` accepts the nested relpath AND the legacy flat name; everything else still returns None. Tasks 2-3 rely on both.

- [ ] **Step 1: Write the failing tests** (add where the existing `parse_clip_name` tests live):

```python
def test_clip_relpath_is_nested_per_source():
    assert clip_relpath(1, 9000, 14000) == "01/9000-14000.mp4"


def test_parse_accepts_the_nested_relpath():
    assert parse_clip_name("01/9000-14000.mp4") == (1, 9000, 14000)


def test_parse_still_accepts_the_legacy_flat_name():
    # Legacy stays parseable on purpose: a flat file left behind by the
    # reconcile sweep's collision case must remain self-identifying so the
    # orphan tooling can report and sweep it.
    assert parse_clip_name("01-9000-14000.mp4") == (1, 9000, 14000)


def test_parse_rejects_everything_else():
    for bad in ("01/9000-14000.mov", ".01/9000-14000.mp4", "a/9000-14000.mp4",
                "01/9000-14000.mp4.part", "01-9000.mp4", "01/02/9000-14000.mp4"):
        assert parse_clip_name(bad) is None
```

- [ ] **Step 2: Run to verify failure** — `~/miniconda3/envs/splitstep/bin/python -m pytest tests/ -q -k "clip_relpath or parse_"` → the new tests FAIL.

- [ ] **Step 3: Implement** in `splitstep/media/clips.py` — keep both docstrings' rationale, extend them:

```python
def clip_relpath(source_idx: int, start_ms: int, end_ms: int) -> str:
    """A clip's path within its session's `clips/` directory: one folder per
    source, span-derived filename inside it.

    [KEEP the existing rationale paragraphs verbatim, then add:]
    The source index is a directory rather than a filename prefix so a
    session holding several phone videos separates them in Finder -- the
    index was always in the name; it moved one level up.
    """
    return f"{source_idx:02d}/{start_ms}-{end_ms}.mp4"


# The two shapes this library has ever written: nested (current) and legacy
# flat (pre layout-reconcile). Both anchored at both ends, same strictness
# rationale as before -- parse admits exactly what we wrote, nothing else.
_CLIP_RELPATH = re.compile(r"^(\d{2,})/(\d+)-(\d+)\.mp4$")
_LEGACY_CLIP_NAME = re.compile(r"^(\d{2,})-(\d+)-(\d+)\.mp4$")


def parse_clip_name(name: str) -> tuple[int, int, int] | None:
    """[KEEP existing rationale; add:] Accepts the legacy flat shape too:
    reconcile_clip_layout leaves a flat file behind when its nested target
    already exists, and that stray must stay self-identifying for the
    orphan sweep rather than becoming invisible dead weight.
    """
    match = _CLIP_RELPATH.match(name) or _LEGACY_CLIP_NAME.match(name)
    if match is None:
        return None
    idx, start_ms, end_ms = match.groups()
    return int(idx), int(start_ms), int(end_ms)
```

- [ ] **Step 4: Run the focused tests** → PASS. Do NOT run the full suite yet — downstream consumers are updated in Tasks 2-3; the suite goes green at Task 3. Ruff the touched files.

- [ ] **Step 5: Commit** — `git add splitstep/media/clips.py tests/<file> && git commit -m "feat(clips): one folder per source in a session's clips"`

---

### Task 2: Orphan walk, reconcile sweep, colour-glob

**Files:**
- Modify: `splitstep/export.py` (`find_orphan_clips` walk + new `reconcile_clip_layout`), `splitstep/jobs/handlers.py` (`_clip_color_profile` glob)
- Test: `tests/test_orphans.py` (walk), new tests in `tests/test_export.py` (reconcile), `tests/test_clips.py` (colour glob)

**Interfaces:**
- Consumes: dual `parse_clip_name`, nested `clip_relpath` (Task 1).
- Produces: `reconcile_clip_layout(library: Library, conn: sqlite3.Connection) -> int` (count moved; 0 on a swept library) in `splitstep/export.py`. Task 3 wires it into the CLI.

- [ ] **Step 1: Failing tests.** In `tests/test_export.py`:

```python
from splitstep.export import reconcile_clip_layout


def _flat_clip(library, session_id, name, content=b"clip"):
    clips = library.clips_dir(session_id)
    clips.mkdir(parents=True, exist_ok=True)
    (clips / name).write_bytes(content)
    return clips / name


def test_reconcile_moves_flat_clips_and_rewrites_clip_path(library, conn):
    _flat_clip(library, "2026-08-18", "01-9000-14000.mp4")
    old_rel = "sessions/2026-08-18/clips/01-9000-14000.mp4"
    conn.execute(
        "INSERT INTO rallies (id, session_id, source_id, idx, start_ms, end_ms, clip_path)"
        " VALUES ('r1', '2026-08-18', 'src', 1, 9000, 14000, ?)", (old_rel,))
    conn.commit()
    assert reconcile_clip_layout(library, conn) == 1
    nested = library.clips_dir("2026-08-18") / "01" / "9000-14000.mp4"
    assert nested.exists()
    row = conn.execute("SELECT clip_path FROM rallies WHERE id='r1'").fetchone()
    assert row["clip_path"] == "sessions/2026-08-18/clips/01/9000-14000.mp4"


def test_reconcile_is_idempotent(library, conn):
    _flat_clip(library, "2026-08-18", "01-9000-14000.mp4")
    assert reconcile_clip_layout(library, conn) == 1
    assert reconcile_clip_layout(library, conn) == 0


def test_reconcile_leaves_a_collision_in_place(library, conn, caplog):
    flat = _flat_clip(library, "2026-08-18", "01-9000-14000.mp4", b"old")
    nested_dir = library.clips_dir("2026-08-18") / "01"
    nested_dir.mkdir(parents=True)
    (nested_dir / "9000-14000.mp4").write_bytes(b"new")
    with caplog.at_level("WARNING"):
        assert reconcile_clip_layout(library, conn) == 0
    assert flat.exists()  # never silently deleted
    assert (nested_dir / "9000-14000.mp4").read_bytes() == b"new"


def test_reconcile_ignores_temp_and_foreign_files(library, conn):
    _flat_clip(library, "2026-08-18", ".01-1-2.abc.part.mp4")
    _flat_clip(library, "2026-08-18", "somebody-elses.mp4")
    assert reconcile_clip_layout(library, conn) == 0
```

(Adjust the rallies INSERT columns to the actual schema — check `001_init.sql`/`009_rally_split.sql` for NOT NULL columns and supply minimal values; the point of the test is the clip_path rewrite, not the row's realism.)

In `tests/test_orphans.py`, add: a nested un-claimed clip (`clips/01/9000-14000.mp4` with no matching rally/reel span) is reported as an orphan, and a stray flat legacy file is too. Follow the file's existing fixture style for creating rallies/claims.

In `tests/test_clips.py`, extend the legacy-HLG test coverage: a library whose only clips are NESTED (`clips/01/1000-2000.mp4`), with no settings row, still locks to legacy HLG (dual glob).

- [ ] **Step 2: Run to verify failures.**

- [ ] **Step 3: Implement.**

`splitstep/export.py` — rework the scan loop inside `find_orphan_clips` (keep the docstring's rationale, update the `iterdir` paragraph to describe the one-level walk) so candidates are top-level files plus files one level down, keyed by their relpath from `clips_dir`:

```python
    candidates: list[Path] = []
    for entry in sorted(clips_dir.iterdir()):
        if entry.is_file():
            candidates.append(entry)
        elif entry.is_dir() and not entry.name.startswith("."):
            candidates.extend(p for p in sorted(entry.iterdir()) if p.is_file())

    orphans = []
    for path in candidates:
        rel = str(path.relative_to(clips_dir))
        if rel in claimed:
            continue
        parsed = parse_clip_name(rel)
        if parsed is None:
            continue
        ...  # unchanged OrphanClip construction
```

(`claimed` already holds nested relpaths since it is built from `clip_relpath`. A stray flat file's rel never matches a nested claimed string, so it parses via the legacy branch and reports as an orphan — exactly the spec's intent.)

Add `reconcile_clip_layout` to `splitstep/export.py` (needs `import logging`, `log = logging.getLogger(__name__)`, and `from splitstep.media.clips import …` already imports `parse_clip_name`):

```python
def reconcile_clip_layout(library: Library, conn: sqlite3.Connection) -> int:
    """One-time move of legacy flat clips into per-source folders.

    File layout, not schema, which is why this is not a numbered migration --
    migrations cannot move files. Runs at serve startup and before every
    clips CLI command, and must run before anything calls plan_export in the
    same process: a claimed flat clip that has not moved yet would read as
    "not cut" and trigger a pointless re-encode. Idempotent -- a swept
    library has no top-level files matching the legacy shape, so the walk
    finds nothing. A collision (nested target already exists) leaves the
    flat file in place and logs it rather than deleting data; the orphan
    tooling can see it (parse_clip_name still admits the legacy shape).
    Renames are same-directory-tree, hence atomic on one filesystem, and the
    matching rallies.clip_path row is rewritten in the same pass so the
    column keeps naming a file that exists.
    """
    moved = 0
    for clips_dir in sorted(library.sessions_dir.glob("*/clips")):
        for path in sorted(clips_dir.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            parsed = parse_clip_name(path.name)
            if parsed is None:
                continue
            source_idx, start_ms, end_ms = parsed
            target = clips_dir / clip_relpath(source_idx, start_ms, end_ms)
            if target.exists():
                log.warning("not moving %s: %s already exists", path, target)
                continue
            target.parent.mkdir(exist_ok=True)
            old_rel = str(path.relative_to(library.root))
            path.rename(target)
            conn.execute(
                "UPDATE rallies SET clip_path = ? WHERE clip_path = ?",
                (str(target.relative_to(library.root)), old_rel),
            )
            conn.commit()
            moved += 1
    if moved:
        log.info("moved %d clip(s) into per-source folders", moved)
    return moved
```

`splitstep/jobs/handlers.py` `_clip_color_profile` — the legacy-clips check becomes both shapes (extend the comment: the glob must find clips whether or not the layout sweep has run yet):

```python
    if any(library.sessions_dir.glob("*/clips/*.mp4")) or any(
        library.sessions_dir.glob("*/clips/*/*.mp4")
    ):
```

- [ ] **Step 4: Run the new tests + `tests/test_orphans.py` + `tests/test_clips.py`** → PASS. Ruff clean.

- [ ] **Step 5: Commit** — `git commit -m "feat(clips): reconcile legacy flat clips into per-source folders"` with the touched files.

---

### Task 3: Wire the sweep, sweep the tests, full suite

**Files:**
- Modify: `splitstep/cli.py` (`cmd_serve`, `cmd_clips_export`, `_load_orphans`), any test hardcoding a flat clip name
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `reconcile_clip_layout` (Task 2).

- [ ] **Step 1: Failing test** in `tests/test_cli.py`:

```python
def test_clips_export_reconciles_legacy_layout_first(library, conn, monkeypatch):
    # A flat legacy clip whose span a point rally claims: without the sweep
    # running before plan_export, this would read as "not cut" and queue a
    # pointless re-encode; with it, the file moves and reads as already cut.
    ...
```

Build it from the file's existing fixtures: create a session+source+rally (see how `test_cli.py`'s clips tests seed data), mark the rally `point=1`, write a flat file at the OLD path for its exact span, run `main(["--library", str(library.root), "clips", "export", <session>])`, then assert the nested file exists and the output says "already cut" with 0 queued.

- [ ] **Step 2: Verify it fails** (flat file stays put, rally queues a clip job).

- [ ] **Step 3: Wire.** In `splitstep/cli.py`:
  - `cmd_serve`: after the library is opened and a connection exists (open one briefly like `cmd_doctor` does: `conn = connect(lib.db_path); migrate(conn)`), call `reconcile_clip_layout(lib, conn)` then `conn.close()` — BEFORE `worker.start()`/`watcher.start()`.
  - `cmd_clips_export` and `_load_orphans`: call `reconcile_clip_layout(library, conn)` right after `migrate(conn)`.
  - Import `reconcile_clip_layout` from `splitstep.export` (merge into the existing import).

- [ ] **Step 4: Hardcoded-name sweep.** `grep -rnE '"[0-9]{2}-[0-9]+-[0-9]+\.mp4"' tests/` — adjudicate each hit: (a) names built via `clip_relpath(...)` need nothing; (b) string literals asserting the on-disk layout update to the nested shape; (c) `tests/test_clips.py`'s legacy-HLG flat-clip test and Task 2's reconcile/orphan legacy fixtures stay FLAT deliberately — they exist to test the legacy path. Report every file touched and its (a)/(b)/(c) class.

- [ ] **Step 5: Full suite + ruff** — `~/miniconda3/envs/splitstep/bin/python -m pytest -q` (foreground, once) → all green; ruff clean.

- [ ] **Step 6: Commit** — `git commit -m "feat(cli): sweep clips into per-source folders before serving or planning"`.
