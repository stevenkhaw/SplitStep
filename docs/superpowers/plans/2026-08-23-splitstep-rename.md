# SplitStep Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-08-23-splitstep-rename-design.md`

**Goal:** Rename the app from BootlegVision to SplitStep everywhere — Python package, CLI, npm package, conda env, library root on disk, repo directory and GitHub repo — leaving no `bootleg` anywhere except three deliberate carve-outs.

**Architecture:** A mechanical three-rule text substitution applied to tracked files only, in four repo-local commits (Python, web, docs, merge), followed by a fifth out-of-repo migration step that is not under version control. Each commit is gated on the existing test suite. There is no new behaviour and therefore no new test: the 680-test suite *is* the test, and the rename is correct exactly when it still passes.

**Tech Stack:** Python 3.12 (conda env), pytest, ruff, Svelte 5 + Vite, vitest, sqlite, ffmpeg, git.

## Global Constraints

- **The three substitution rules, applied in this order, always:**
  ```
  BootlegVision  -> SplitStep
  bootlegvision  -> splitstep
  bootleg        -> splitstep
  ```
  Order is load-bearing. `bootlegvision` must be rewritten before the bare `bootleg` rule or it becomes `splitstepvision`. Verified: these are the only three spellings in the main tree — no bare capitalised `Bootleg`, no ALLCAPS `BOOTLEG`.
- **Operate on tracked files only.** Every sweep selects files via `git ls-files`. This is what keeps `node_modules/`, `.git/`, `__pycache__/`, `bootleg.egg-info/` and the two stale worktrees under `.claude/worktrees/` out of the blast radius. Never sweep by `find` or bare `grep -r`.
- **macOS sed requires an argument to `-i`.** Every command below uses `sed -i ''`. Dropping the `''` creates backup files named after the next flag.
- **Four files are excluded from every sweep**, listed in full in the Carve-Outs section below. Two are data-not-name; two are the rename documents themselves.
- **Baseline is 680 tests collected, ruff clean.** Confirmed on master at commit `47640cc` before any change. Note that `CLAUDE.md` currently claims 612 — it is stale, and Task 3 corrects it.
- **Python is invoked by interpreter path**, never bare, because the conda env is not the shell default. Before Task 1 that path is `~/miniconda3/envs/bootleg/bin/`; from Task 1 Step 8 onward it is `~/miniconda3/envs/splitstep/bin/`.
- **Branch `rename/splitstep` already exists** and already holds the spec commit `b732d47`. Do not create it again.
- **This plan file must be committed before Task 3 runs.** Task 3's sweep selects through `git ls-files`, which lists tracked files only — an uncommitted plan is invisible to both the sweep and its exclusion filter. Committing it first is what makes the exclusion in Task 3 Step 2 mean anything, and it takes the swept markdown count from 21 to 20.

## Carve-Outs — never sweep these four files

| File | Why |
|---|---|
| `web/public/label.html` | Contains `const KEY = "bootleg-labels-v1"`, a localStorage key. Data, not a name. Renaming it orphans browser data from the superseded 2026-08-20 validation tool and buys nothing. **The `<title>` on line 3 is swept by hand instead** — see Task 2 Step 4. |
| `docs/superpowers/specs/2026-08-23-splitstep-rename-design.md` | Records the substitution rules verbatim. Sweeping it rewrites `BootlegVision -> SplitStep` into `SplitStep -> SplitStep` and turns §3's filename sentence into a circular reference, destroying the rules the document exists to record. |
| `docs/superpowers/plans/2026-08-23-splitstep-rename.md` | This file. Same reason. |
| `library.db` (`jobs` table, two rows) | Two `status=done` ingest rows from 2026-08-20 whose payload JSON embeds the old absolute inbox path. Never read again; ingest already moved those files. Not a tracked file, so `git ls-files` excludes it automatically — listed here so nobody "fixes" it later. |

## File Structure

Nothing is created or restructured. Every file below is modified in place or moved.

**Moved:**
- `bootleg/` → `splitstep/` (whole package, 39 tracked `.py` files across `api/`, `db/`, `detect/`, `jobs/`, `media/`)
- `docs/superpowers/specs/2026-08-19-bootlegvision-design.md` → `2026-08-19-splitstep-design.md`

**Modified — Task 1 (Python):**
- `splitstep/**/*.py` — 116 hits: 109 distinct import lines plus 57 comment/string references
- `tests/**/*.py` — 239 hits, including 14 string-based `monkeypatch.setattr("bootleg.…")` targets that resolve at call time rather than import time
- `pyproject.toml:2` (`name = "bootleg"`) and `:19` (`bootleg = "bootleg.cli:main"`)

**Modified — Task 2 (Web):**
- `web/package.json:2`, `web/package-lock.json:2` and `:7` — the `"bootleg-web"` name fields
- `web/index.html:6` — `<title>`
- `web/public/label.html:3` — `<title>` only, by hand
- `web/src/lib/*.ts` (10 files), `web/src/components/QuadEditor.svelte`, `web/tests/labels.test.ts` — cross-reference comments pointing at backend files by path, plus two user-facing `CLI: bootleg detect` strings

**Modified — Task 3 (Docs):**
- `CLAUDE.md` (20 hits), `README.md` (22), `HANDOFF.md` (9)
- `docs/superpowers/specs/*.md` and `docs/superpowers/plans/*.md` — 19 files, 788 hits

**Not in git — Task 5:**
- `.claude/launch.json` (untracked; `.claude/` shows as untracked in `git status`)
- `/Volumes/SanDisk_2TB/BootlegVision` → `.../SplitStep`
- `~/Documents/GitHub/BootlegVision` → `.../SplitStep`
- `~/.claude/projects/-Users-stevenkhaw-Documents-GitHub-BootlegVision` → matching new slug
- conda envs `bootleg` (removed) and `splitstep` (created)

---

### Task 1: Python package, imports, and the new conda env

**Files:**
- Move: `bootleg/` → `splitstep/`
- Modify: `splitstep/**/*.py`, `tests/**/*.py`, `pyproject.toml`
- Test: the whole existing suite — `tests/`

**Interfaces:**
- Consumes: nothing. This is the first task.
- Produces: the importable package `splitstep`, the console script `splitstep`, and the conda env `splitstep` at `~/miniconda3/envs/splitstep/`. Every later task and every command in Tasks 2–5 depends on that interpreter path. The module layout is unchanged, so `from splitstep.config import Library` is exactly the old `from bootleg.config import Library` and no signature anywhere changes.

- [ ] **Step 1: Confirm the starting state**

```bash
cd /Users/stevenkhaw/Documents/GitHub/BootlegVision
git branch --show-current
git status --short
~/miniconda3/envs/bootleg/bin/python -m pytest --collect-only -q 2>&1 | tail -1
```

Expected: branch is `rename/splitstep`; `git status --short` shows only `?? .claude/`; collection reports `680 tests collected`. If the count differs from 680, stop and reconcile — every gate below compares against this number.

- [ ] **Step 2: Move the package**

```bash
git mv bootleg splitstep
```

Note there is a `splitstep/setup.py` after this move. That is the pipeline's setup-wizard module (`queue_setup`), not a packaging script — the build backend is configured in `pyproject.toml` and ignores it. Do not "clean it up".

- [ ] **Step 3: Sweep the Python sources and packaging**

```bash
git ls-files -z 'splitstep/*.py' 'tests/*.py' 'pyproject.toml' \
  | xargs -0 sed -i '' \
      -e 's/BootlegVision/SplitStep/g' \
      -e 's/bootlegvision/splitstep/g' \
      -e 's/bootleg/splitstep/g'
```

A git pathspec is not a shell glob: `*` matches `/` as well, so `splitstep/*.py`
reaches all 39 tracked `.py` files including `splitstep/api/app.py` and
`splitstep/db/labels.py`, with no `**` needed. Verified before this plan was
written. The same property is what keeps the 8 `.sql` migrations in
`splitstep/db/migrations/` out of the sweep — they are matched by
`splitstep/*` but not by `splitstep/*.py`. That is deliberate: `CLAUDE.md`
forbids editing an applied migration, and the migrations were separately
verified to contain no occurrence of the name, so there is nothing in them to
change anyway.

- [ ] **Step 4: Verify no Python straggler remains**

```bash
git ls-files -z 'splitstep/*' 'tests/*' 'pyproject.toml' | xargs -0 grep -nI bootleg
```

Expected: no output, exit status 1. Any hit is a file the glob missed — most likely a `.py` nested deeper than the glob reached. Widen and re-run Step 3 before continuing.

- [ ] **Step 5: Confirm the packaging entries landed correctly**

```bash
sed -n '1,3p;18,20p' pyproject.toml
```

Expected to contain `name = "splitstep"` and `splitstep = "splitstep.cli:main"`. Both halves of the script line must have changed — the left side is the command name, the right side is the module path.

- [ ] **Step 6: Confirm the string-based patch targets were rewritten**

These are the ones that would not fail at import time. There are 14.

```bash
grep -rn 'monkeypatch.setattr("splitstep' tests/ --include='*.py' | wc -l
```

Expected: `14`. A lower number means the sweep missed some and they will fail later as `ModuleNotFoundError` deep inside a test rather than at collection.

- [ ] **Step 7: Delete the stale editable-install metadata**

```bash
rm -rf bootleg.egg-info
```

It is gitignored (`.gitignore:19`), so this does not appear in any diff. Leaving it behind means two competing `*.egg-info` directories once the new install runs.

- [ ] **Step 8: Clone the conda env and install the renamed package into it**

```bash
conda create -n splitstep --clone bootleg -y
```

Then, inside the new env, drop the old editable install before adding the new one — the clone carries `__editable__.bootleg-0.1.0.pth` and its finder module, which would otherwise keep resolving the old name:

```bash
~/miniconda3/envs/splitstep/bin/pip uninstall -y bootleg
~/miniconda3/envs/splitstep/bin/pip install -e '.[dev]'
```

The env is cloned rather than built fresh so that torch, ultralytics and opencv are copied from local packages instead of re-resolved from PyPI. A fresh resolve risks versions the detector was never validated against, and per `CLAUDE.md` the detector's tuning is already known to be fragile.

- [ ] **Step 9: Verify the install**

```bash
ls ~/miniconda3/envs/splitstep/lib/python3.12/site-packages/ | grep -i -e splitstep -e bootleg
~/miniconda3/envs/splitstep/bin/splitstep --help | head -3
```

Expected: the listing shows `__editable__.splitstep-0.1.0.pth`, `__editable___splitstep_0_1_0_finder.py` and `splitstep-0.1.0.dist-info`, and **no** `bootleg` entry. The help output's usage line reads `usage: splitstep` — that comes from `prog="splitstep"` in `cli.py:490` and confirms the sweep reached the argparse setup, not just the imports.

- [ ] **Step 10: Run the full suite**

```bash
~/miniconda3/envs/splitstep/bin/pytest -q
```

Expected: `680 passed`. This takes 20+ minutes — the e2e tests run real ffmpeg 4K encodes. Do not pipe it to `tail`; the exit code is lost through the pipe. Let it run to completion and read the final line.

- [ ] **Step 11: Run ruff**

```bash
~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
```

Expected: `All checks passed!`

- [ ] **Step 12: Commit**

```bash
git add -A splitstep tests pyproject.toml
git commit -m "refactor: rename the bootleg package to splitstep

Package directory, every import, the console script and the argparse
prog name. No behaviour changes and no signature changes -- the module
layout is identical, so this is the three-rule substitution and nothing
else. 680 tests pass against a cloned conda env with the package
reinstalled editable under the new name.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Web app

**Files:**
- Modify: `web/package.json`, `web/package-lock.json`, `web/index.html`, `web/src/lib/*.ts`, `web/src/components/QuadEditor.svelte`, `web/tests/labels.test.ts`
- Modify by hand: `web/public/label.html` (line 3 only)
- Test: `web/tests/`

**Interfaces:**
- Consumes: nothing from Task 1 at runtime. The frontend talks to the backend over HTTP and the API route paths do not contain the app name, so no endpoint changes. What it consumes is *documentary*: ~20 comments in `web/src/lib/` point at backend files by path (`// see bootleg/db/labels.py`), and those paths only became valid again after Task 1's `git mv`.
- Produces: `web/dist/`, which `splitstep serve` mounts at `/`. Task 5's smoke test depends on this build existing.

- [ ] **Step 1: Sweep the web sources, excluding `label.html`**

```bash
cd /Users/stevenkhaw/Documents/GitHub/BootlegVision
git ls-files -z 'web/*' \
  | grep -zv '^web/public/label.html$' \
  | xargs -0 sed -i '' \
      -e 's/BootlegVision/SplitStep/g' \
      -e 's/bootlegvision/splitstep/g' \
      -e 's/bootleg/splitstep/g'
```

`git ls-files` does not list `web/node_modules/` or `web/dist/` (both gitignored), so the sweep cannot reach them. The `grep -zv` is what protects the localStorage key.

- [ ] **Step 2: Verify the package name fields changed in both files**

```bash
grep -n '"name"' web/package.json | head -1
grep -n '"splitstep-web"' web/package-lock.json
```

Expected: `package.json` line 2 reads `"name": "splitstep-web",`, and the lockfile shows two hits, at lines 2 and 7. Both lockfile occurrences must change — line 2 is the root name and line 7 is the `packages[""]` self-entry, and npm warns on a mismatch between them.

- [ ] **Step 3: Verify the two user-facing CLI strings changed**

```bash
grep -n 'CLI: splitstep detect' web/src/components/QuadEditor.svelte
```

Expected: two hits, at roughly lines 121 and 144. These are strings shown to you in the quad editor when a region changes, telling you to re-run detect. A stale command here is a command that no longer exists.

- [ ] **Step 4: Fix the `label.html` title by hand**

The file is excluded from the sweep to protect its storage key, so its title needs a targeted edit. Line 3 currently reads:

```html
<title>BootlegVision — clip labelling</title>
```

Change it to:

```html
<title>SplitStep — clip labelling</title>
```

Then confirm the key survived:

```bash
grep -n 'bootleg-labels-v1' web/public/label.html
```

Expected: one hit on line 58, still reading `const KEY = "bootleg-labels-v1";`. If this is gone, the sweep leaked past the exclusion — revert the file and redo Step 1.

- [ ] **Step 5: Verify no other web straggler remains**

```bash
git ls-files -z 'web/*' | xargs -0 grep -nI bootleg
```

Expected: exactly one line — `web/public/label.html:58:const KEY = "bootleg-labels-v1";`. Anything else is a miss.

- [ ] **Step 6: Type and a11y check**

```bash
cd web && npm run check
```

Expected: `svelte-check found 0 errors`. The comment sweep cannot break types, so any error here means a string the sweep touched was load-bearing.

- [ ] **Step 7: Run the frontend tests**

```bash
cd web && npx vitest run
```

Expected: all files pass. `web/tests/labels.test.ts` has a comment referencing the backend path and is the only test file the sweep touched.

- [ ] **Step 8: Build**

```bash
cd web && npm run build
```

Expected: writes `web/dist/` with no errors. This is what `serve` mounts, and Task 5 Step 4 loads it.

- [ ] **Step 9: Commit**

```bash
cd /Users/stevenkhaw/Documents/GitHub/BootlegVision
git add -A web
git commit -m "refactor(web): rename to splitstep

npm package name in package.json and both lockfile entries, the page
title, the two 'CLI: bootleg detect' strings in the quad editor, and
the cross-reference comments in lib/ that point at backend files by
path -- those paths moved in the previous commit and a pointer to a
path that no longer exists is worse than no pointer.

label.html keeps its localStorage key 'bootleg-labels-v1'. It is data,
not a name; renaming it would orphan whatever that superseded
validation tool still holds in the browser. Its title is updated by
hand instead.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Documentation

**Files:**
- Move: `docs/superpowers/specs/2026-08-19-bootlegvision-design.md` → `docs/superpowers/specs/2026-08-19-splitstep-design.md`
- Modify: `CLAUDE.md`, `README.md`, `HANDOFF.md`, `docs/superpowers/specs/*.md`, `docs/superpowers/plans/*.md`
- Modify by hand: `CLAUDE.md` (the stale test count)
- Test: none — markdown does not execute. The gate is a completeness grep.

**Interfaces:**
- Consumes: Task 1's package layout, since `CLAUDE.md` and the docs quote real file paths (`splitstep/detect/segment.py`) and real commands that must now work.
- Produces: nothing code depends on.

- [ ] **Step 1: Move the one file that carries the name in its filename**

```bash
cd /Users/stevenkhaw/Documents/GitHub/BootlegVision
git mv docs/superpowers/specs/2026-08-19-bootlegvision-design.md \
       docs/superpowers/specs/2026-08-19-splitstep-design.md
```

Nine references to the old filename live in seven other documents. They are **not** fixed by hand — the `bootlegvision -> splitstep` rule in the next step rewrites them, which is precisely why the new filename was chosen to match what that rule produces. Filename and references therefore cannot drift.

- [ ] **Step 2: Sweep all documentation except the two rename documents**

```bash
git ls-files -z '*.md' \
  | grep -zv '^docs/superpowers/specs/2026-08-23-splitstep-rename-design.md$' \
  | grep -zv '^docs/superpowers/plans/2026-08-23-splitstep-rename.md$' \
  | xargs -0 sed -i '' \
      -e 's/BootlegVision/SplitStep/g' \
      -e 's/bootlegvision/splitstep/g' \
      -e 's/bootleg/splitstep/g'
```

The two exclusions are mandatory. Both documents quote the substitution rules literally; sweeping them rewrites `BootlegVision -> SplitStep` into `SplitStep -> SplitStep` and collapses the Carve-Outs table into nonsense.

- [ ] **Step 3: Verify the nine filename references resolved**

```bash
grep -rn '2026-08-19-splitstep-design' docs/ HANDOFF.md | wc -l
grep -rn '2026-08-19-bootlegvision-design' docs/ HANDOFF.md
```

Expected: `9` from the first command, and no output from the second.

- [ ] **Step 4: Correct the stale test count in `CLAUDE.md`**

`CLAUDE.md` documents the suite as 612 tests. The real figure measured on master at `47640cc` is 680. The sweep does not touch a bare number, so this is a hand edit. Line 20 currently reads (with the env path already rewritten by Step 2):

```
~/miniconda3/envs/splitstep/bin/pytest -q                              # 680 tests
```

That is the target state. Before the edit it reads `# 612 tests`. Confirm and fix:

```bash
grep -n '612 tests' CLAUDE.md
```

Expected before the edit: one hit on line 20. Expected after: no output.

This correction is unrelated to the rename but is made here because the file is already being edited, and leaving a known-false number in the project's own instructions is worse than fixing it in passing.

- [ ] **Step 5: Update the interpreter paths in `CLAUDE.md`**

The sweep rewrote `~/miniconda3/envs/bootleg/bin/pytest` to `~/miniconda3/envs/splitstep/bin/pytest` automatically, since the env name matches the package name. Confirm:

```bash
grep -n 'miniconda3/envs' CLAUDE.md
```

Expected: every path reads `envs/splitstep/bin/…`. Confirm too that the library-path examples now read `--library /Volumes/SanDisk_2TB/SplitStep`, which Task 5 Step 1 makes true on disk.

- [ ] **Step 6: Verify the whole tree is clean**

```bash
git ls-files -z | xargs -0 grep -lI bootleg
```

Expected: exactly three files —
```
docs/superpowers/plans/2026-08-23-splitstep-rename.md
docs/superpowers/specs/2026-08-23-splitstep-rename-design.md
web/public/label.html
```
All three are documented carve-outs. Any fourth file is a miss.

- [ ] **Step 7: Re-run the Python suite**

```bash
~/miniconda3/envs/splitstep/bin/pytest -q
```

Expected: `680 passed`. Documentation changes cannot break tests, but this run is what certifies the branch as mergeable, and it costs nothing but time.

- [ ] **Step 8: Commit**

```bash
git add -A CLAUDE.md README.md HANDOFF.md docs
git commit -m "docs: rename to splitstep throughout

Sweeps all 788 hits across the specs and plans, and moves
2026-08-19-bootlegvision-design.md to 2026-08-19-splitstep-design.md.
Its nine inbound references resolve under the same substitution rule
that produced the new filename, so the two cannot drift.

The docs double as the reference manual and are full of runnable
command lines that would rot on the day of the rename. The dated
rename spec is the record of the old name; it and this plan are the
only markdown excluded, because they quote the substitution rules
verbatim and sweeping them would destroy what they document.

Also corrects the suite size in CLAUDE.md from 612 to the measured 680.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Merge to master

**Files:** none modified. Branch integration only.

**Interfaces:**
- Consumes: the four commits from Tasks 1–3 plus the spec commit `b732d47`.
- Produces: a master branch whose working tree is what Task 5 renames on disk.

- [ ] **Step 1: Confirm the branch is complete and clean**

```bash
cd /Users/stevenkhaw/Documents/GitHub/BootlegVision
git status --short
git log --oneline master..rename/splitstep
```

Expected: `git status --short` shows only `?? .claude/`; the log lists four commits — the spec, then Python, web, docs.

- [ ] **Step 2: Check whether master moved underneath you**

```bash
git log --oneline -1 master
```

Expected: `47640cc`. Other agents commit directly to master in this repo, so if this is not `47640cc`, do **not** fast-forward — rebase `rename/splitstep` onto the new master first and re-run Task 1 Step 10 before merging.

- [ ] **Step 3: Fast-forward master**

```bash
git switch master
git merge --ff-only rename/splitstep
git log --oneline -5
```

Expected: the merge succeeds without creating a merge commit, and the log shows the four commits on top of `47640cc`.

- [ ] **Step 4: Push**

```bash
git push origin master
```

`origin/master` is currently one commit behind local master (`68a5c64` vs `47640cc`) — an unpushed merge unrelated to this work. Push it now rather than during Task 5, so the push and the GitHub repo rename do not interleave.

---

### Task 5: Out-of-repo migration

**Files:**
- Modify: `.claude/launch.json` (untracked)
- Move: the library root, the repo directory, the Claude Code project directory
- Remove: the `bootleg` conda env

**Interfaces:**
- Consumes: the `splitstep` console script from Task 1 and `web/dist/` from Task 2.
- Produces: nothing further depends on this. It is the last step.

Nothing in this task is under version control, so each sub-step is verified before the next. Sub-steps 1, 5 and 6 reverse with the inverse `mv`. Sub-step 4 is the only one-way door in the entire plan.

- [ ] **Step 1: Confirm nothing is running, then rename the library**

```bash
pgrep -fl 'bootleg|splitstep' || echo "nothing running"
```

Expected: `nothing running`. If a `serve` process is up it holds the sqlite file and the watcher is polling `_inbox/`; renaming the directory underneath it corrupts neither but will crash the worker mid-job. Stop it first.

```bash
mv /Volumes/SanDisk_2TB/BootlegVision /Volumes/SanDisk_2TB/SplitStep
ls /Volumes/SanDisk_2TB/SplitStep
```

Expected listing: `_backups  _inbox  library.db  reels  sessions`.

- [ ] **Step 2: Update `.claude/launch.json`**

Both fields change — the executable path embeds the conda env name and the argument is the library root. The file should read:

```json
{
  "version": "0.0.1",
  "configurations": [
    {
      "name": "splitstep",
      "runtimeExecutable": "/Users/stevenkhaw/miniconda3/envs/splitstep/bin/splitstep",
      "runtimeArgs": ["--library", "/Volumes/SanDisk_2TB/SplitStep", "serve"],
      "port": 8420
    }
  ]
}
```

- [ ] **Step 3: Smoke test against the renamed library**

```bash
~/miniconda3/envs/splitstep/bin/splitstep --library /Volumes/SanDisk_2TB/SplitStep doctor
```

Expected: passes, reporting the library as healthy. This is the real proof of the spec's central claim — that `rallies.clip_path` and `reels.rendered_path` are relative and `sources.original_name` is a bare filename, so moving the root needed no database rewrite.

If `doctor` reports missing files, **stop and reverse Step 1** (`mv` the directory back). Do not proceed to Step 4; the old env is still the working fallback until then.

- [ ] **Step 4: Remove the old conda env**

Only after Step 3 passes. This is the single irreversible action in the plan, and it is optional — skipping it costs nothing but disk.

```bash
conda env remove -n bootleg -y
conda env list
```

Expected: `splitstep` is listed, `bootleg` is not.

- [ ] **Step 5: Rename the GitHub repo and update the remote**

The GitHub-side rename is done by hand in the repository's settings page, by the repo owner. It is not scripted here: it changes a public URL, and it is the one step in this plan that touches something outside this machine.

Once GitHub reports the rename complete:

```bash
cd /Users/stevenkhaw/Documents/GitHub/BootlegVision
git remote set-url origin https://github.com/stevenkhaw/SplitStep.git
git remote -v
git fetch origin
```

Expected: both remote lines show `SplitStep.git`, and the fetch succeeds. GitHub redirects the old URL indefinitely, so there is no window in which the remote is broken — the `set-url` is hygiene, not a repair.

- [ ] **Step 6: Rename the repo directory and the Claude Code project directory together**

This is last because it pulls the working directory out from under any running session. Run both `mv` commands in one go:

```bash
mv ~/Documents/GitHub/BootlegVision ~/Documents/GitHub/SplitStep
mv ~/.claude/projects/-Users-stevenkhaw-Documents-GitHub-BootlegVision \
   ~/.claude/projects/-Users-stevenkhaw-Documents-GitHub-SplitStep
```

The second directory holds 17 session transcripts and five memory files, and Claude Code keys it on the repo path. Renaming one without the other orphans them.

- [ ] **Step 7: Final verification from the new location**

```bash
cd ~/Documents/GitHub/SplitStep
git status --short
git log --oneline -1
ls ~/.claude/projects/-Users-stevenkhaw-Documents-GitHub-SplitStep/memory/
~/miniconda3/envs/splitstep/bin/splitstep --library /Volumes/SanDisk_2TB/SplitStep doctor
```

Expected: git is clean and on the merged master; the memory listing shows `MEMORY.md` and the five memory files; `doctor` passes.

- [ ] **Step 8: Note the two stale worktrees**

```bash
git worktree list
```

The two worktrees under `.claude/worktrees/` (`intelligent-allen-d6cb99`, `nostalgic-mayer-e46895`) still contain full pre-rename copies of the tree and their own `bootleg/` package directories. They were deliberately outside the blast radius — every sweep selected files through `git ls-files` in the main tree. They are stale checkouts of already-merged branches. Report them; do not remove them without asking.

---

## Verification Summary

| Task | Gate | Expected |
|---|---|---|
| 1 | `pytest -q` in the new env | `680 passed` |
| 1 | `ruff check splitstep tests` | `All checks passed!` |
| 1 | `splitstep --help` | usage line reads `usage: splitstep` |
| 2 | `npm run check` | 0 errors |
| 2 | `npx vitest run` | all pass |
| 2 | `npm run build` | writes `web/dist/` |
| 3 | `git ls-files -z \| xargs -0 grep -lI bootleg` | exactly the 3 carve-out files |
| 3 | `pytest -q` | `680 passed` |
| 4 | `git merge --ff-only` | no merge commit |
| 5 | `splitstep … doctor` | passes against the renamed library |
