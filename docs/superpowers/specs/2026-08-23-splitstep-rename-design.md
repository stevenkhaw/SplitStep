# SplitStep rename — design

**Date:** 2026-08-23
**Status:** Approved and implemented — the package, the CLI and the library
tree are all `splitstep` throughout.
**Supersedes the name established in:** `docs/superpowers/specs/2026-08-19-splitstep-design.md`
(named `2026-08-19-bootlegvision-design.md` until this work landed)

The app is renamed from BootlegVision to **SplitStep**, everywhere, in one pass.
This document records what changes, in what order, and — more usefully — the
three places where the string `bootleg` is *data* rather than a name, because
those are the only places a blind sweep would do damage.

## 1. Why a full rename rather than a cosmetic one

The alternative considered was renaming only the user-visible surfaces (window
title, README, GitHub repo) and leaving the Python package, the CLI command and
the imports as `bootleg`. Rejected: this is a single-user local app with no
published package, no downstream consumers and no API contract to anyone. There
is no cost to a clean break and a permanent cost to a codebase whose package
name contradicts its product name. Every future reader would have to learn both.

A compatibility shim — keeping `bootleg` as an alias module and console script —
was also considered and rejected for the same reason. Nothing imports this
package except this package.

## 2. Naming map

| Surface | From | To |
|---|---|---|
| Display name | BootlegVision | SplitStep |
| Python package | `bootleg/` | `splitstep/` |
| Imports | `from bootleg.x import` | `from splitstep.x import` |
| CLI command / `prog=` | `bootleg` | `splitstep` |
| `pyproject.toml` name + script | `bootleg` | `splitstep` |
| npm package | `bootleg-web` | `splitstep-web` |
| Conda env | `bootleg` | `splitstep` |
| Library root | `/Volumes/SanDisk_2TB/BootlegVision` | `/Volumes/SanDisk_2TB/SplitStep` |
| Repo directory | `~/Documents/GitHub/BootlegVision` | `~/Documents/GitHub/SplitStep` |
| GitHub repo | `stevenkhaw/BootlegVision` | `stevenkhaw/SplitStep` |

### Case variants

The main tree contains exactly three spellings, verified by
`grep -rhio 'bootleg[a-z]*' | sort -u`. There is no bare capitalised `Bootleg`
and no ALLCAPS `BOOTLEG`. The substitution is therefore three ordered rules:

```
BootlegVision  -> SplitStep
bootlegvision  -> splitstep
bootleg        -> splitstep
```

Order is load-bearing. `bootlegvision` must be rewritten before the bare
`bootleg` rule, or it becomes `splitstepvision`. `BootlegVision` is
case-independent of both and safe in any position, but is listed first for
symmetry.

## 3. Scope: everything, including the docs

`docs/superpowers/` carries 788 of the ~1220 main-tree hits across dated specs
and plans. These are swept too.

This was argued both ways. The case for leaving them: they are a dated decision
log, and a 2026-08-19 design doc naming SplitStep asserts a name that did not
exist on that date, in exactly the place you go to reconstruct why a decision
was made. The case for sweeping, which won: the docs are also the reference
manual, they are full of runnable `bootleg ...` command lines that would rot on
the day of the rename, and a reader hitting two names for one app in the same
repo is worse off than one reading a lightly anachronistic date line. This
document is the timestamp that records the old name.

One file carries the name in its filename:
`docs/superpowers/specs/2026-08-19-bootlegvision-design.md`, referenced nine
times across seven other documents. It is `git mv`'d to
`2026-08-19-splitstep-design.md`; the sweep fixes all nine references as a side
effect of the `bootlegvision -> splitstep` rule, so the rename and the
references cannot drift apart.

## 4. What does not change, and why

These are the only places where `bootleg` is data. A sweep must skip them.

**`bootleg/db/migrations/*.sql`** — verified to contain no occurrence of the
name, so there is nothing to change. Recorded here because CLAUDE.md forbids
editing an applied migration and a future reader should know the question was
asked and answered rather than overlooked.

**`web/public/label.html`, `const KEY = "bootleg-labels-v1"`** — a localStorage
key, not a name. `label.html` is the superseded one-off validation tool from the
2026-08-20 camera-viewpoint work; its findings were already harvested into
`docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md`. Renaming the
key would silently orphan whatever that page still holds in the browser and buys
nothing, since no user-visible string is involved. The key keeps the old spelling
deliberately.

**Two rows in `library.db`'s `jobs` table** — both `status=done` ingest jobs from
2026-08-20, whose payload JSON embeds the absolute inbox path under the old
library root. Never read again: ingest already moved those files, and the rows
exist as history. A stale path in a finished job row is an accurate record of
where that file actually was. Left alone.

Every other path stored in the database is relative, confirmed by query rather
than assumed. The schema has exactly three columns that could hold one:
`rallies.clip_path` reads `sessions/2026-08-18/clips/01-18279-26287.mp4`,
`reels.rendered_path` reads `reels/2026-08-18-points.mp4`, and
`sources.original_name` holds a bare filename (`IMG_2373.MOV`) with zero rows
containing a slash. This is what makes step 6 below a plain `mv` with no
database rewrite. No file on the SSD carries the name either; only the library
root directory itself does.

## 5. Execution order

Six steps. The ordering exists so that every step is verifiable before the next
one depends on it, and so that the two steps that cannot be undone by `git` come
last.

**1. Branch.** `rename/splitstep` off master. Per the established workflow this
merges fast-forward into master at step 5; there is no PR.

**2. Python.** `git mv bootleg splitstep`, apply the three substitution rules
across `splitstep/` and `tests/`, update `pyproject.toml` (project name and the
`[project.scripts]` entry). Then `conda create -n splitstep --clone bootleg`
followed by `pip install -e '.[dev]'` inside it. The clone is used rather than a
fresh env so that torch, ultralytics and opencv are copied locally instead of
re-resolved from PyPI — a fresh env risks pulling versions the detector was
never validated against, and the detector's tuning is already known to be
fragile.

*Verify:* `~/miniconda3/envs/splitstep/bin/pytest -q` reports 612 passed, and
`~/miniconda3/envs/splitstep/bin/ruff check splitstep tests` is clean. The old
`bootleg` env is untouched at this point and remains the fallback.

**3. Web.** `package.json` and `package-lock.json` name fields, the `<title>` in
`index.html` and `public/label.html`, the two `CLI: bootleg detect` strings in
`QuadEditor.svelte`, and the ~20 cross-reference comments in `web/src/lib/`
that point at backend files by path (`// see bootleg/db/labels.py`). Those
comments are swept rather than skipped: they are file pointers, and a pointer to
a path that no longer exists is worse than no pointer. The `label.html`
localStorage key is the sole exclusion, per §4.

*Verify:* `npm run check`, `npx vitest run`, `npm run build` all clean.

**4. Documentation.** `CLAUDE.md`, `README.md`, `HANDOFF.md`, and the full
`docs/superpowers/` tree including the `git mv` of the design doc filename.

*Verify:* `grep -ri bootleg` over the main tree returns only
`web/public/label.html`'s storage key.

**5. Merge.** Fast-forward `rename/splitstep` into master.

**6. Outside the repo.** Nothing in this step is under version control, so each
sub-step is verified before the next:

  a. `mv /Volumes/SanDisk_2TB/BootlegVision /Volumes/SanDisk_2TB/SplitStep`
  b. Update `.claude/launch.json` — both the `runtimeExecutable` path (which
     embeds the conda env name) and the `--library` argument.
  c. Smoke test: `splitstep --library /Volumes/SanDisk_2TB/SplitStep doctor`.
     This is the real proof that the relative-path claim in §4 holds.
  d. `conda env remove -n bootleg`. The only irreversible action in the plan,
     and it happens only after (c) passes. Skippable at no cost.
  e. GitHub: the repo is renamed by hand in GitHub's settings, then
     `git remote set-url origin`. GitHub redirects the old URL indefinitely, so
     there is no window in which the remote is broken.
  f. `mv ~/Documents/GitHub/BootlegVision ~/Documents/GitHub/SplitStep`, and in
     the same breath `mv` the Claude Code project directory
     `~/.claude/projects/-Users-stevenkhaw-Documents-GitHub-BootlegVision` to
     the matching new slug. That directory holds 17 session transcripts and the
     five memory files; both are keyed on the repo path, and renaming one
     without the other orphans them.

Step 6f is last because it pulls the working directory out from under any
running session.

## 6. Reversibility

Steps 1–5 are `git`-reversible in full. Step 6a, 6e and 6f reverse with the
inverse `mv` or rename. Step 6d is the single one-way door, which is why it sits
behind a passing smoke test and is optional.

The unpushed state of `origin/master` at the time of writing (local master is
one merge commit ahead) is unrelated to this work but is worth resolving before
step 6e, so that the GitHub rename and the push do not interleave.

## 7. Verification summary

| Step | Gate |
|---|---|
| 2 | 612 pytest pass; ruff clean |
| 3 | `npm run check`, vitest, `npm run build` clean |
| 4 | `grep -ri bootleg` returns only the localStorage key |
| 6c | `splitstep ... doctor` passes against the renamed library |
