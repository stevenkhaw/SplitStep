# Rally Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `C` in timeline mode cuts the current rally in two at the playhead; `U` merges a hand-made half back.

**Architecture:** A rally with `det_start_ms IS NULL` is one a human made and the detector never proposed. That single fact does all the work: it keeps the two halves from colliding in `rally_labels` (which anchors on `(source_id, det_start_ms, det_end_ms)`), it is what `merge_into_previous` checks so merge can never delete a row the corpus is anchored to, and it is what `LabelController` filters on. Migration `009` rebuilds `rallies` to make the pair nullable. Two new db functions, both ending in the existing `_renumber`. Two new routes. One new pure frontend module.

**Tech Stack:** Python 3.12 + sqlite3 + Starlette/FastAPI routes; Svelte 5 + TypeScript + Vitest.

**Spec:** `docs/superpowers/specs/2026-08-23-rally-split-design.md`

## Global Constraints

- Python runs from the `splitstep` conda env by path: `~/miniconda3/envs/splitstep/bin/pytest`, `~/miniconda3/envs/splitstep/bin/ruff`. It is **not** the shell default.
- `pytest` runs with `filterwarnings = ["error"]`. A new warning fails the suite.
- ruff line-length is **100**.
- Migrations are numbered `.sql` files in `splitstep/db/migrations/`, applied by `PRAGMA user_version`. **Add a file; never edit an applied one.**
- Comments explain **why**, not what. This codebase carries long rationale comments on non-obvious calls. Match that density.
- All frontend logic lives in `web/src/lib/` as pure TypeScript. Components are thin shells — jsdom has no `<video>`, so anything inside a `.svelte` file is untestable.
- No raw Tailwind palette steps (`bg-neutral-800`) or arbitrary sizes (`text-[11px]`). Only tokens from `web/src/app.css`.
- Frontend commands run from `web/`: `npx vitest run`, `npm run check`.
- Every API route is `def`, never `async def`.
- `det_start_ms` / `det_end_ms` are immutable for any rally that has them. Nothing in this plan ever writes a det span to a row that lacks one, or changes one that has one.

---

## File Structure

**Create:**
- `splitstep/db/migrations/009_rally_split.sql` — the table rebuild making `det_*` nullable
- `tests/test_split.py` — db-layer split/merge behaviour
- `web/src/lib/split.ts` — the four pure client functions
- `web/tests/split.test.ts` — their tests

**Modify:**
- `splitstep/db/rallies.py` — add `split_rally`, `merge_into_previous`
- `splitstep/api/routes.py` — add `/split`, `/merge`; make `_rally_det_span` det-less-aware; skip the corpus write in `/bounds` and `/label`
- `splitstep/db/labels.py` — no change (verify only)
- `tests/test_db.py` — five `migrate(conn) == 8` assertions become `== 9`; add the 009 rebuild test
- `tests/test_api_labels.py` — a drag on a det-less rally writes no corpus row
- `web/src/lib/types.ts` — widen `det_start_ms` / `det_end_ms` to `number | null`
- `web/src/lib/api.ts` — `splitRally`, `mergeRally`
- `web/src/lib/shortcuts.ts` — `Split` group in `TIMELINE`; new `PRIMARY.timeline`
- `web/src/lib/resegment.ts` — `splitCount`; exclude det-less from `editedBoundaryCount`; widen the message
- `web/src/lib/labels.ts` — `LabelController` filters det-less rallies in its constructor
- `web/src/components/TimelineMode.svelte` — `C` / `U` cases and their handlers
- `web/tests/resegment.test.ts` — split-vs-boundary count
- `web/tests/labels.test.ts` — controller filters det-less
- `CLAUDE.md` — document the det-less rally in the conventions section

---

## Task 1: Migration 009 — nullable det span

**Files:**
- Create: `splitstep/db/migrations/009_rally_split.sql`
- Modify: `tests/test_db.py:49-50`, `:159`, `:219`, `:277` (the `== 8` assertions)
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing
- Produces: `rallies.det_start_ms` / `det_end_ms` nullable, with `CHECK ((det_start_ms IS NULL) = (det_end_ms IS NULL))`. Every later task depends on this.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db.py`:

```python
def test_migration_009_rebuilds_rallies_without_losing_rows(tmp_path):
    # 009 makes det_start_ms/det_end_ms nullable, which sqlite can only do by
    # rebuilding the table -- create, copy, drop, rename. A rebuild that
    # forgot the copy would take an entire session's review work with it
    # (stars, points, notes, seen_at) and nothing would notice until the
    # queue reopened empty. Migrate to 008, plant a fully-populated rally,
    # then let 009 run over it.
    conn = connect(tmp_path / "old.db")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        n = int(path.name.split("_", 1)[0])
        if n > 8:
            break
        conn.executescript(path.read_text())
        conn.execute(f"PRAGMA user_version={n}")
    conn.execute(
        "INSERT INTO sessions (id,title,played_on,status,created_at)"
        " VALUES ('s1','t','2026-08-19','ready','now')"
    )
    conn.execute(
        "INSERT INTO sources (id,session_id,idx,recorded_at,offset_ms,duration_ms,"
        "width,height,fps,rotation_deg,original_name,status)"
        " VALUES ('src1','s1',1,'now',0,1000,1920,1080,30.0,0,'a.mov','ready')"
    )
    conn.execute(
        "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
        "det_start_ms,det_end_ms,confidence,starred,rejected,point,reviewed_at,"
        "clip_path,note,seen_at) VALUES ('r1','s1','src1',1,1100,5200,1000,5000,"
        "0.83,1,0,1,'T1','clips/a.mp4','late backhand','T0')"
    )
    conn.commit()

    assert migrate(conn) == 9

    row = conn.execute("SELECT * FROM rallies").fetchone()
    # Every column, not just the two being altered: the whole risk of a
    # rebuild is a column dropped from the INSERT ... SELECT copy.
    assert (row["id"], row["session_id"], row["source_id"], row["idx"]) == (
        "r1", "s1", "src1", 1)
    assert (row["start_ms"], row["end_ms"]) == (1100, 5200)
    assert (row["det_start_ms"], row["det_end_ms"]) == (1000, 5000)
    assert row["confidence"] == 0.83
    assert (row["starred"], row["rejected"], row["point"]) == (1, 0, 1)
    assert (row["reviewed_at"], row["seen_at"]) == ("T1", "T0")
    assert (row["clip_path"], row["note"]) == ("clips/a.mp4", "late backhand")
    # The rebuild must not have quietly disabled enforcement for the rest of
    # this connection's life -- 009 toggles no pragmas, same as 004.
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migration_009_allows_a_null_det_span(conn):
    # The point of the migration: a rally a human made, which the detector
    # never proposed. NULL det is the marker -- see the spec's section 3.
    conn.execute(
        "INSERT INTO sessions (id,title,played_on,status,created_at)"
        " VALUES ('s2','t','2026-08-19','ready','now')"
    )
    conn.execute(
        "INSERT INTO sources (id,session_id,idx,recorded_at,offset_ms,duration_ms,"
        "width,height,fps,rotation_deg,original_name,status)"
        " VALUES ('src2','s2',1,'now',0,1000,1920,1080,30.0,0,'b.mov','ready')"
    )
    conn.execute(
        "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
        "det_start_ms,det_end_ms,confidence) VALUES"
        " ('r2','s2','src2',1,1000,5000,NULL,NULL,0.5)"
    )
    conn.commit()
    row = conn.execute("SELECT * FROM rallies WHERE id='r2'").fetchone()
    assert row["det_start_ms"] is None
    assert row["det_end_ms"] is None


def test_migration_009_rejects_a_half_present_det_span(conn):
    # Both or neither. A half-present span is a third state nothing knows how
    # to read: every consumer asks `det_start_ms IS NULL` and that question
    # must answer for the pair.
    conn.execute(
        "INSERT INTO sessions (id,title,played_on,status,created_at)"
        " VALUES ('s3','t','2026-08-19','ready','now')"
    )
    conn.execute(
        "INSERT INTO sources (id,session_id,idx,recorded_at,offset_ms,duration_ms,"
        "width,height,fps,rotation_deg,original_name,status)"
        " VALUES ('src3','s3',1,'now',0,1000,1920,1080,30.0,0,'c.mov','ready')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
            "det_start_ms,det_end_ms,confidence) VALUES"
            " ('r3','s3','src3',1,1000,5000,1000,NULL,0.5)"
        )
```

Add to `tests/test_db.py`'s imports if absent: `import sqlite3` and `import pytest`.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_db.py -q -k migration_009
```

Expected: FAIL. `test_migration_009_rebuilds_rallies_without_losing_rows` fails on `assert migrate(conn) == 9` (gets `8`); the other two fail because `det_start_ms` is still `NOT NULL`.

- [ ] **Step 3: Write the migration**

Create `splitstep/db/migrations/009_rally_split.sql`:

```sql
-- det_start_ms/det_end_ms become nullable, and NULL acquires a meaning:
-- "no detector ever proposed this rally; a human made it."
--
-- This exists so timeline mode can cut one rally in two. The detector
-- merges two rallies whenever the break between them is too short to score
-- below threshold -- correctly, since the alternative tuning shreds real
-- rallies -- and until now the reviewer had no way to disagree, because
-- moving a boundary needs a second rally to move it TO.
--
-- Why the second half carries no det span, rather than inheriting the
-- first's: rally_labels anchors on (source_id, det_start_ms, det_end_ms) --
-- the detector's own span, which is what lets the corpus survive
-- replace_rallies (see 003). Two halves sharing one det span would be the
-- same row in the corpus, and labelling the second would silently overwrite
-- the judgement on the first. Giving each half its own det span covering
-- its own bounds is worse still: det_* is immutable and records what the
-- detector ORIGINALLY GUESSED, so writing spans it never produced
-- fabricates training data and feeds `labels score` candidates with no
-- basis in any detector run.
--
-- The absence of a span is the marker rather than a boolean beside it,
-- because a boolean can drift out of agreement with the columns it
-- describes and an absence cannot. Every consumer that needs to ask "is
-- this a detector proposal?" already reads det_*, and now gets a truthful
-- answer without knowing splits exist.
--
-- Rebuilding `rallies` is safe: nothing in the schema declares REFERENCES
-- rallies. reel_items keys on the span (007) and rally_labels.rally_id
-- deliberately carries no foreign key (003) -- both for the same reason,
-- that replace_rallies deletes every rally for a source on each sweep.
CREATE TABLE rallies_new (
  id            TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  source_id     TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  idx           INTEGER NOT NULL,
  start_ms      INTEGER NOT NULL,
  end_ms        INTEGER NOT NULL,
  det_start_ms  INTEGER,
  det_end_ms    INTEGER,
  confidence    REAL    NOT NULL,
  starred       INTEGER NOT NULL DEFAULT 0,
  rejected      INTEGER NOT NULL DEFAULT 0,
  point         INTEGER NOT NULL DEFAULT 0,
  reviewed_at   TEXT,
  clip_path     TEXT,
  note          TEXT NOT NULL DEFAULT '',
  seen_at       TEXT,

  -- Both or neither, never one. A half-present det span is a third state
  -- nothing knows how to read: `det_start_ms IS NULL` is the question every
  -- consumer asks, and it must answer for the pair.
  CHECK ((det_start_ms IS NULL) = (det_end_ms IS NULL)),
  UNIQUE(session_id, idx)
);

INSERT INTO rallies_new (id, session_id, source_id, idx, start_ms, end_ms,
  det_start_ms, det_end_ms, confidence, starred, rejected, point, reviewed_at,
  clip_path, note, seen_at)
SELECT id, session_id, source_id, idx, start_ms, end_ms,
  det_start_ms, det_end_ms, confidence, starred, rejected, point, reviewed_at,
  clip_path, note, seen_at
FROM rallies;

DROP TABLE rallies;
ALTER TABLE rallies_new RENAME TO rallies;

CREATE INDEX idx_rallies_session ON rallies(session_id, idx);
CREATE INDEX idx_rallies_source  ON rallies(source_id);
CREATE INDEX idx_rallies_starred ON rallies(starred) WHERE starred = 1;
CREATE INDEX idx_rallies_point   ON rallies(point) WHERE point = 1;
```

- [ ] **Step 4: Update the five migration-count assertions**

In `tests/test_db.py`, change every `assert migrate(conn) == 8` to `assert migrate(conn) == 9`. There are five, at lines 49, 50, 159, 219, 277.

```bash
sed -i '' 's/assert migrate(conn) == 8/assert migrate(conn) == 9/g' tests/test_db.py
grep -c "assert migrate(conn) == 9" tests/test_db.py
```

Expected: `5`

- [ ] **Step 5: Run the full db suite**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_db.py -q
```

Expected: PASS, all tests.

- [ ] **Step 6: Run the whole suite — the rebuild touches every rally consumer**

```bash
~/miniconda3/envs/splitstep/bin/pytest -q
```

Expected: PASS. If anything fails here it is the rebuild dropping a column, not a flaky test.

- [ ] **Step 7: Lint and commit**

```bash
~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
git add splitstep/db/migrations/009_rally_split.sql tests/test_db.py
git commit -m "feat(db): let a rally carry no detector span"
```

---

## Task 2: `split_rally`

**Files:**
- Modify: `splitstep/db/rallies.py`
- Test: `tests/test_split.py` (create)

**Interfaces:**
- Consumes: migration `009` from Task 1; the existing `_renumber(conn, session_id)` and `_now()` in the same module.
- Produces: `split_rally(conn: sqlite3.Connection, rally_id: str, at_ms: int) -> str`, returning the new (second half's) rally id. Raises `ValueError` on an unknown rally or an `at_ms` outside `start_ms < at_ms < end_ms`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_split.py`:

```python
"""Cutting one rally in two, and merging a hand-made half back.

The detector merges two rallies whenever the break between them scores
above threshold -- correctly, since the tuning that separates them shreds
real rallies. Timeline mode could previously only move a merged rally's two
boundaries, so the reviewer's only options were to trim down to one half
and lose the other, or keep one oversized clip.

The half a human cuts carries NO detector span. rally_labels anchors on
(source_id, det_start_ms, det_end_ms), so two halves inheriting one det span
would collide in the corpus and the second labelled would silently overwrite
the first. See the spec, section 3.
"""
import pytest

from splitstep.db.rallies import (
    list_rallies,
    merge_into_previous,
    replace_rallies,
    set_clip_path,
    set_note,
    set_point,
    set_star,
    split_rally,
)
from splitstep.db.sessions import add_source, find_or_create_session_for_date
from splitstep.detect.segment import Interval


def _seeded(conn, name="IMG_9100.MOV"):
    session_id = find_or_create_session_for_date(conn, "2026-08-21")
    source_id, _idx = add_source(
        conn, session_id, recorded_at="2026-08-21T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name=name,
    )
    return session_id, source_id


def test_split_produces_two_abutting_halves(conn):
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    rally_id = list_rallies(conn, session_id)[0]["id"]

    new_id = split_rally(conn, rally_id, 5000)

    rows = list_rallies(conn, session_id)
    assert len(rows) == 2
    assert (rows[0]["start_ms"], rows[0]["end_ms"]) == (1000, 5000)
    assert (rows[1]["start_ms"], rows[1]["end_ms"]) == (5000, 9000)
    assert rows[1]["id"] == new_id
    # The original row survives as the first half rather than being deleted
    # and re-inserted: it is the one holding the detector's provenance.
    assert rows[0]["id"] == rally_id


def test_second_half_carries_no_detector_span(conn):
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    rally_id = list_rallies(conn, session_id)[0]["id"]

    split_rally(conn, rally_id, 5000)

    rows = list_rallies(conn, session_id)
    # First half keeps the detector's ORIGINAL span, unchanged -- not
    # narrowed to its new bounds. det_* records what the detector guessed.
    assert (rows[0]["det_start_ms"], rows[0]["det_end_ms"]) == (1000, 9000)
    assert rows[1]["det_start_ms"] is None
    assert rows[1]["det_end_ms"] is None


def test_split_inherits_every_review_flag(conn):
    # Not an arbitrary choice: this is what replace_rallies' own carry-over
    # rule produces for these two intervals. overlap_fraction divides by the
    # SHORTER span, so each half sits fully inside the parent and scores a
    # flat 1.0, clearing STAR_OVERLAP_MIN outright. A split rally must behave
    # exactly as it would had the detector proposed both intervals.
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    rally_id = list_rallies(conn, session_id)[0]["id"]
    set_star(conn, rally_id, True)
    set_point(conn, rally_id, True)
    set_note(conn, rally_id, "late backhand")

    split_rally(conn, rally_id, 5000)

    second = list_rallies(conn, session_id)[1]
    assert second["starred"] == 1
    assert second["point"] == 1
    assert second["note"] == "late backhand"
    assert second["confidence"] == 0.8
    assert second["seen_at"] is not None
    assert second["reviewed_at"] is not None


def test_split_nulls_clip_path_on_both_halves(conn):
    # _carried_clip_path demands an EXACT span match for exactly this reason:
    # the 4K file on disk was cut at the old span and describes neither half.
    # The orphaned file is `clips prune`'s problem -- see set_clip_path's
    # docstring, which names that command as its only consumer.
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    rally_id = list_rallies(conn, session_id)[0]["id"]
    set_clip_path(conn, rally_id, "clips/01-1000-9000.mp4")

    split_rally(conn, rally_id, 5000)

    rows = list_rallies(conn, session_id)
    assert rows[0]["clip_path"] is None
    assert rows[1]["clip_path"] is None


@pytest.mark.parametrize("at_ms", [1000, 9000, 500, 12000])
def test_split_refuses_a_cut_outside_the_rally(conn, at_ms):
    # Strictly inside, so both halves are non-empty. The MIN_RALLY_MS floor
    # is deliberately NOT enforced here -- /bounds validates only
    # end_ms > start_ms and leaves the floor to clampMinGap client-side, and
    # media/concat.py states outright that a hand-trimmed clip well under
    # 1.5s is a real input. The server rejects the incoherent, not the tiny.
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    rally_id = list_rallies(conn, session_id)[0]["id"]

    with pytest.raises(ValueError):
        split_rally(conn, rally_id, at_ms)

    assert len(list_rallies(conn, session_id)) == 1


def test_split_refuses_an_unknown_rally(conn):
    _seeded(conn)
    with pytest.raises(ValueError):
        split_rally(conn, "nope", 5000)


def test_split_renumbers_across_two_sources(conn):
    # The case _renumber's two-phase negative-placeholder dance exists for:
    # growing a NON-LAST source's rally count walks straight into the next
    # source's still-live idx under UNIQUE(session_id, idx).
    session_id, src_a = _seeded(conn, "IMG_9100.MOV")
    src_b, _idx = add_source(
        conn, session_id, recorded_at="2026-08-21T11:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9101.MOV",
    )
    replace_rallies(conn, session_id, src_a,
                    [Interval(1000, 9000, 0.8), Interval(20000, 25000, 0.7)])
    replace_rallies(conn, session_id, src_b, [Interval(1000, 4000, 0.9)])
    first_of_a = list_rallies(conn, session_id)[0]["id"]

    split_rally(conn, first_of_a, 5000)

    rows = list_rallies(conn, session_id)
    assert [r["idx"] for r in rows] == [1, 2, 3, 4]
    # Ordered by source idx, then start_ms -- the new half lands second, and
    # source B's rally is pushed from 3 to 4 rather than colliding with it.
    assert [(r["source_id"], r["start_ms"]) for r in rows] == [
        (src_a, 1000), (src_a, 5000), (src_a, 20000), (src_b, 1000),
    ]


def test_split_leaves_the_set_untouched_when_it_fails(conn, monkeypatch):
    # One transaction. A half-applied split -- new row inserted, renumber
    # never run -- would sit on the shared connection violating
    # UNIQUE(session_id, idx) until some unrelated later commit persisted it.
    import splitstep.db.rallies as rallies_mod

    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    rally_id = list_rallies(conn, session_id)[0]["id"]

    def boom(*_args, **_kwargs):
        raise RuntimeError("renumber exploded")

    monkeypatch.setattr(rallies_mod, "_renumber", boom)
    with pytest.raises(RuntimeError):
        split_rally(conn, rally_id, 5000)

    rows = list_rallies(conn, session_id)
    assert len(rows) == 1
    assert (rows[0]["start_ms"], rows[0]["end_ms"]) == (1000, 9000)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_split.py -q
```

Expected: FAIL at import — `cannot import name 'split_rally' from 'splitstep.db.rallies'`.

- [ ] **Step 3: Implement `split_rally`**

Add to `splitstep/db/rallies.py`, after `replace_rallies` and before `_renumber`:

```python
def split_rally(conn: sqlite3.Connection, rally_id: str, at_ms: int) -> str:
    """Cut one rally in two at `at_ms`. Returns the new (second) rally's id.

    The second half carries NO detector span. rally_labels anchors on
    (source_id, det_start_ms, det_end_ms) -- the detector's own span, which
    is what lets the corpus survive replace_rallies -- so two halves
    inheriting one det span would be the same row in the corpus, and
    labelling the second would silently overwrite the judgement on the
    first. Giving each half its own det span covering its own bounds is
    worse: det_* is immutable and records what the detector ORIGINALLY
    guessed, so spans it never produced are fabricated training data.

    `at_ms` must sit strictly inside the rally, so both halves are
    non-empty. The MIN_RALLY_MS floor is deliberately not enforced here --
    the bounds route validates only end_ms > start_ms and leaves the floor
    to clampMinGap client-side (see media/concat.py, which spells out that a
    hand-trimmed clip well under 1.5s is a real input). This layer rejects
    what is incoherent, not what is merely short.

    Every review flag is inherited, which is not a fresh judgement call: it
    is what replace_rallies' own carry-over produces for these two
    intervals, since overlap_fraction divides by the shorter span and each
    half sits fully inside the parent at a flat 1.0. A split rally therefore
    behaves exactly as it would had the detector proposed both intervals.

    clip_path is the one exception, for the same reason _carried_clip_path
    demands an exact span match rather than an overlap: the file on disk was
    cut at the old span and describes neither half. Both are nulled; the
    orphaned file is `clips prune`'s job.
    """
    row = conn.execute("SELECT * FROM rallies WHERE id = ?", (rally_id,)).fetchone()
    if row is None:
        raise ValueError(f"No such rally: {rally_id}")
    if not row["start_ms"] < at_ms < row["end_ms"]:
        raise ValueError(
            f"Cut at {at_ms}ms is not strictly inside rally "
            f"{row['start_ms']}-{row['end_ms']}ms"
        )

    new_id = uuid.uuid4().hex
    try:
        # A per-row-unique negative placeholder, the same technique
        # replace_rallies uses: the real idx cannot be assigned until
        # _renumber runs, and any positive value here risks colliding with a
        # live row under UNIQUE(session_id, idx).
        conn.execute(
            "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
            "det_start_ms,det_end_ms,confidence,starred,rejected,point,"
            "reviewed_at,clip_path,note,seen_at)"
            " VALUES (?,?,?,?,?,?,NULL,NULL,?,?,?,?,?,NULL,?,?)",
            (new_id, row["session_id"], row["source_id"], -1, at_ms, row["end_ms"],
             row["confidence"], row["starred"], row["rejected"], row["point"],
             row["reviewed_at"], row["note"], row["seen_at"]),
        )
        conn.execute(
            "UPDATE rallies SET end_ms = ?, clip_path = NULL WHERE id = ?",
            (at_ms, rally_id),
        )
        _renumber(conn, row["session_id"])
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return new_id
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_split.py -q -k "split"
```

Expected: PASS for every `test_split*` and `test_second_half*`. The merge tests do not exist yet.

- [ ] **Step 5: Lint and commit**

```bash
~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
git add splitstep/db/rallies.py tests/test_split.py
git commit -m "feat(db): cut one rally in two at a chosen millisecond"
```

---

## Task 3: `merge_into_previous`

**Files:**
- Modify: `splitstep/db/rallies.py`
- Test: `tests/test_split.py`

**Interfaces:**
- Consumes: `split_rally` from Task 2; `_renumber`.
- Produces: `merge_into_previous(conn: sqlite3.Connection, rally_id: str) -> None`. Raises `ValueError` on an unknown rally, a rally carrying a det span, or a rally with no abutting predecessor in the same source.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_split.py`:

```python
def test_merge_puts_a_split_rally_back_together(conn):
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    rally_id = list_rallies(conn, session_id)[0]["id"]
    new_id = split_rally(conn, rally_id, 5000)

    merge_into_previous(conn, new_id)

    rows = list_rallies(conn, session_id)
    assert len(rows) == 1
    assert (rows[0]["id"], rows[0]["start_ms"], rows[0]["end_ms"]) == (rally_id, 1000, 9000)
    # Merging back does not restore provenance to a rally that never lost
    # it: the survivor's det span is the one it always had.
    assert (rows[0]["det_start_ms"], rows[0]["det_end_ms"]) == (1000, 9000)
    assert rows[0]["idx"] == 1


def test_merge_nulls_the_survivors_clip_path(conn):
    # Same reason split does: the row's span just changed, so a file cut at
    # the old span no longer describes it.
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    rally_id = list_rallies(conn, session_id)[0]["id"]
    new_id = split_rally(conn, rally_id, 5000)
    set_clip_path(conn, rally_id, "clips/01-1000-5000.mp4")

    merge_into_previous(conn, new_id)

    assert list_rallies(conn, session_id)[0]["clip_path"] is None


def test_merge_refuses_a_rally_carrying_a_detector_span(conn):
    # The whole safety story. Merge can only ever undo something a human
    # made; it must never delete a row the label corpus is anchored to.
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(5000, 9000, 0.7)])
    second = list_rallies(conn, session_id)[1]["id"]

    with pytest.raises(ValueError):
        merge_into_previous(conn, second)

    assert len(list_rallies(conn, session_id)) == 2


def test_merge_refuses_a_non_abutting_predecessor(conn):
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    rally_id = list_rallies(conn, session_id)[0]["id"]
    new_id = split_rally(conn, rally_id, 5000)
    # Trim the first half's tail, opening a gap. The two rows no longer
    # describe one contiguous stretch of footage, so rejoining them would
    # invent play across the gap.
    set_bounds(conn, rally_id, 1000, 4000)

    with pytest.raises(ValueError):
        merge_into_previous(conn, new_id)


def test_merge_refuses_when_the_previous_rally_is_another_source(conn):
    session_id, src_a = _seeded(conn, "IMG_9100.MOV")
    src_b, _idx = add_source(
        conn, session_id, recorded_at="2026-08-21T11:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9101.MOV",
    )
    replace_rallies(conn, session_id, src_a, [Interval(1000, 5000, 0.8)])
    replace_rallies(conn, session_id, src_b, [Interval(5000, 9000, 0.7)])
    # Hand-make a det-less first rally on source B so the det guard passes
    # and the source guard is what has to refuse.
    b_first = list_rallies(conn, session_id)[1]["id"]
    conn.execute(
        "UPDATE rallies SET det_start_ms = NULL, det_end_ms = NULL WHERE id = ?",
        (b_first,),
    )
    conn.commit()

    with pytest.raises(ValueError):
        merge_into_previous(conn, b_first)


def test_merge_refuses_an_unknown_rally(conn):
    _seeded(conn)
    with pytest.raises(ValueError):
        merge_into_previous(conn, "nope")


def test_a_twice_split_rally_collapses_in_reverse(conn):
    # Nested splits fall out of the rules rather than needing a case of
    # their own: split_rally never reads det_*, and merge tests the TARGET's
    # det span, not the previous rally's. The one row carrying provenance is
    # never a legal merge target at any step.
    session_id, source_id = _seeded(conn)
    replace_rallies(conn, session_id, source_id, [Interval(1000, 9000, 0.8)])
    original = list_rallies(conn, session_id)[0]["id"]
    second = split_rally(conn, original, 5000)
    third = split_rally(conn, second, 7000)

    assert len(list_rallies(conn, session_id)) == 3

    merge_into_previous(conn, third)
    merge_into_previous(conn, second)

    rows = list_rallies(conn, session_id)
    assert len(rows) == 1
    assert (rows[0]["id"], rows[0]["start_ms"], rows[0]["end_ms"]) == (original, 1000, 9000)

    # And the one that still has provenance stays un-mergeable.
    with pytest.raises(ValueError):
        merge_into_previous(conn, original)
```

Add `set_bounds` to the `splitstep.db.rallies` import block at the top of the file.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_split.py -q
```

Expected: FAIL at import — `cannot import name 'merge_into_previous'`.

- [ ] **Step 3: Implement `merge_into_previous`**

Add to `splitstep/db/rallies.py`, immediately after `split_rally`:

```python
def merge_into_previous(conn: sqlite3.Connection, rally_id: str) -> None:
    """Undo a split: absorb `rally_id` into the rally that abuts it.

    Refuses unless the target carries NO detector span. That guard is the
    whole safety story -- merge can only ever undo something a human made in
    this session, and can never delete a row rally_labels is anchored to.
    It is also why the inverse of split is not "merge any two adjacent
    rallies": that more useful-sounding operation would let one keystroke
    destroy detector provenance, and fixing detector OVER-segmentation is a
    different feature with a different risk profile.

    The predecessor must abut exactly. Once the reviewer has trimmed the
    seam, the two rows no longer describe one contiguous stretch of footage
    and rejoining them would invent play across the gap they opened.

    The survivor's det_* is untouched -- merging back does not restore
    provenance to a rally that never lost it, nor invent it for one that
    never had it. Its clip_path is nulled for the same reason split_rally
    nulls it: the row's span just changed. The target's flags are discarded
    rather than merged, since split_rally made them inherited copies of the
    survivor's own.
    """
    row = conn.execute("SELECT * FROM rallies WHERE id = ?", (rally_id,)).fetchone()
    if row is None:
        raise ValueError(f"No such rally: {rally_id}")
    if row["det_start_ms"] is not None:
        raise ValueError(
            f"Rally {rally_id} carries a detector span; only a hand-made "
            f"rally can be merged back"
        )
    prev = conn.execute(
        "SELECT id FROM rallies WHERE source_id = ? AND end_ms = ? AND id != ?",
        (row["source_id"], row["start_ms"], rally_id),
    ).fetchone()
    if prev is None:
        raise ValueError(
            f"Rally {rally_id} has no rally abutting its start in the same source"
        )

    try:
        conn.execute(
            "UPDATE rallies SET end_ms = ?, clip_path = NULL WHERE id = ?",
            (row["end_ms"], prev["id"]),
        )
        conn.execute("DELETE FROM rallies WHERE id = ?", (rally_id,))
        _renumber(conn, row["session_id"])
    except Exception:
        conn.rollback()
        raise
    conn.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_split.py -q
```

Expected: PASS, all tests in the file.

- [ ] **Step 5: Run the whole suite**

```bash
~/miniconda3/envs/splitstep/bin/pytest -q
```

Expected: PASS.

- [ ] **Step 6: Lint and commit**

```bash
~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
git add splitstep/db/rallies.py tests/test_split.py
git commit -m "feat(db): merge a hand-made half back into the rally it came from"
```

---

## Task 4: API routes, and the det-less skip in `/bounds` and `/label`

**Files:**
- Modify: `splitstep/api/routes.py`
- Test: `tests/test_api_review.py`, `tests/test_api_labels.py`

**Interfaces:**
- Consumes: `split_rally`, `merge_into_previous` from Tasks 2 and 3.
- Produces: `POST /api/rallies/{id}/split` taking `{"at_ms": int}` and returning `{"ok": true, "new_rally_id": str}`; `POST /api/rallies/{id}/merge` returning `{"ok": true}`. `_rally_det_span` returns `None` for a det-less rally instead of a row.

- [ ] **Step 1: Write the failing tests**

Both files already have a `seeded` fixture (`tests/test_api_review.py:38`,
`tests/test_api_labels.py:20`) that seeds two rallies at `Interval(1000, 5000)`
and `Interval(9000, 14000)`. Every cut below is at **3000ms** — strictly inside
the first rally. Do not use 5000: that is the rally's own `end_ms` and the
route correctly refuses it.

Append to `tests/test_api_review.py`:

```python
def _first_rally_id(conn):
    return conn.execute("SELECT id FROM rallies ORDER BY idx").fetchone()["id"]


def test_split_route_returns_the_new_rally_id(client, conn, seeded):
    rally_id = _first_rally_id(conn)
    r = client.post(f"/api/rallies/{rally_id}/split", json={"at_ms": 3000})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["new_rally_id"], str) and body["new_rally_id"]
    rows = conn.execute("SELECT start_ms, end_ms FROM rallies ORDER BY idx").fetchall()
    assert [(r["start_ms"], r["end_ms"]) for r in rows] == [
        (1000, 3000), (3000, 5000), (9000, 14000)]


def test_split_route_400s_on_a_cut_outside_the_rally(client, conn, seeded):
    rally_id = _first_rally_id(conn)
    r = client.post(f"/api/rallies/{rally_id}/split", json={"at_ms": 999_999})
    assert r.status_code == 400
    assert "strictly inside" in r.json()["detail"]


def test_split_route_400s_on_a_cut_at_the_boundary(client, conn, seeded):
    # 5000 is the rally's own end_ms. A zero-length half is incoherent, not
    # merely short -- see split_rally on why the MIN_RALLY_MS floor is the
    # client's job and this one is the server's.
    rally_id = _first_rally_id(conn)
    r = client.post(f"/api/rallies/{rally_id}/split", json={"at_ms": 5000})
    assert r.status_code == 400


def test_split_route_404s_on_an_unknown_rally(client, seeded):
    r = client.post("/api/rallies/nope/split", json={"at_ms": 3000})
    assert r.status_code == 404


def test_merge_route_puts_the_halves_back(client, conn, seeded):
    rally_id = _first_rally_id(conn)
    new_id = client.post(f"/api/rallies/{rally_id}/split", json={"at_ms": 3000}).json()[
        "new_rally_id"
    ]
    r = client.post(f"/api/rallies/{new_id}/merge")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    rows = conn.execute("SELECT start_ms, end_ms FROM rallies ORDER BY idx").fetchall()
    assert [(r["start_ms"], r["end_ms"]) for r in rows] == [(1000, 5000), (9000, 14000)]


def test_merge_route_400s_on_a_rally_with_a_detector_span(client, conn, seeded):
    # The guard that keeps a keystroke from deleting a row the corpus is
    # anchored to has to survive the trip through HTTP, not just the db call.
    rally_id = _first_rally_id(conn)
    r = client.post(f"/api/rallies/{rally_id}/merge")
    assert r.status_code == 400
    assert "detector span" in r.json()["detail"]
```

Append to `tests/test_api_labels.py` (which already defines `_first_rally(conn)`
returning the whole row):

```python
def test_a_drag_on_a_hand_made_half_writes_no_corpus_row(client, conn, seeded):
    # /bounds records every drag as a signed detector error, anchored to
    # det_start_ms/det_end_ms. A hand-made half has none, so there is nothing
    # to anchor to and no detector error to measure. Without this skip the
    # route would write a row keyed on (NULL, NULL) -- a key nothing can ever
    # resolve against, quietly accumulating in an append-only table.
    rally_id = _first_rally(conn)["id"]
    new_id = client.post(f"/api/rallies/{rally_id}/split", json={"at_ms": 3000}).json()[
        "new_rally_id"
    ]
    before = conn.execute("SELECT COUNT(*) FROM rally_labels").fetchone()[0]

    # The new half spans 3000-5000; this drag trims both its edges.
    r = client.post(f"/api/rallies/{new_id}/bounds", json={"start_ms": 3200, "end_ms": 4800})

    assert r.status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM rally_labels").fetchone()[0] == before
    row = conn.execute("SELECT start_ms, end_ms FROM rallies WHERE id = ?", (new_id,)).fetchone()
    assert (row["start_ms"], row["end_ms"]) == (3200, 4800)


def test_a_drag_on_the_first_half_still_records_its_correction(client, conn, seeded):
    # The other side of the same skip: the half that KEPT the detector span
    # is still ordinary ground truth, and the split must not have cost the
    # corpus that. Its det span is the parent's original 1000-5000.
    rally_id = _first_rally(conn)["id"]
    client.post(f"/api/rallies/{rally_id}/split", json={"at_ms": 3000})
    before = conn.execute("SELECT COUNT(*) FROM rally_labels").fetchone()[0]

    r = client.post(f"/api/rallies/{rally_id}/bounds", json={"start_ms": 1200, "end_ms": 3000})

    assert r.status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM rally_labels").fetchone()[0] == before + 1
    row = conn.execute(
        "SELECT span_start_ms, span_end_ms, true_start_ms FROM rally_labels"
        " ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert (row["span_start_ms"], row["span_end_ms"]) == (1000, 5000)
    assert row["true_start_ms"] == 1200


def test_labelling_a_hand_made_half_is_refused(client, conn, seeded):
    rally_id = _first_rally(conn)["id"]
    new_id = client.post(f"/api/rallies/{rally_id}/split", json={"at_ms": 3000}).json()[
        "new_rally_id"
    ]
    r = client.post(f"/api/rallies/{new_id}/label", json={"verdict": "clean",
                                                         "boundary_flags": []})
    assert r.status_code == 400
    assert "detector" in r.json()["detail"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_api_review.py tests/test_api_labels.py -q
```

Expected: FAIL — 404 on the unknown `/split` route.

- [ ] **Step 3: Add the request body and the two routes**

In `splitstep/api/routes.py`, add to the imports from `splitstep.db.rallies`: `merge_into_previous`, `split_rally`.

Add beside the other body models (after `BoundsBody`):

```python
class SplitBody(BaseModel):
    at_ms: int
```

Add after `api_bounds`:

```python
@router.post("/api/rallies/{rally_id}/split")
def api_split(rally_id: str, body: SplitBody, request: Request):
    """Cut one rally in two. The second half carries no detector span.

    404 and 400 are separated deliberately: an unknown id is a stale client
    (a re-segment in another tab already deleted the rally), while a bad
    at_ms is a live client asking for something incoherent. The reviewer's
    recovery differs -- reload versus move the playhead -- so the two must
    not collapse into one status.
    """
    conn = _conn(request)
    row = conn.execute("SELECT id FROM rallies WHERE id = ?", (rally_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Rally not found")
    try:
        new_id = split_rally(conn, rally_id, body.at_ms)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "new_rally_id": new_id}


@router.post("/api/rallies/{rally_id}/merge")
def api_merge(rally_id: str, request: Request):
    """Absorb a hand-made half back into the rally that abuts it.

    Refused for a rally carrying a detector span -- see
    merge_into_previous. Same 404/400 split as api_split, for the same
    reason.
    """
    conn = _conn(request)
    row = conn.execute("SELECT id FROM rallies WHERE id = ?", (rally_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Rally not found")
    try:
        merge_into_previous(conn, rally_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}
```

- [ ] **Step 4: Make `_rally_det_span` det-less-aware**

Replace `_rally_det_span` in `splitstep/api/routes.py:346-360` with:

```python
def _rally_det_span(conn, rally_id: str):
    """The rally's immutable detector span, `None` if it has none, or 404.

    Every label anchors to det_start_ms/det_end_ms rather than the editable
    start_ms/end_ms, so this is resolved server-side and clients never send a
    span -- a client that computed it from stale rally data could otherwise
    anchor a judgement to a span the detector never produced.

    A hand-made half of a split rally has no detector span at all (see
    split_rally). Returning None rather than a row of NULLs forces every
    caller to decide what that means instead of writing a corpus row keyed
    on (NULL, NULL) -- a key nothing can ever resolve against, accumulating
    silently in an append-only table.
    """
    row = conn.execute(
        "SELECT source_id, det_start_ms, det_end_ms FROM rallies WHERE id = ?",
        (rally_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Rally not found")
    if row["det_start_ms"] is None:
        return None
    return row
```

- [ ] **Step 5: Handle `None` in `/bounds` and `/label`**

In `api_bounds` (`splitstep/api/routes.py:419-452`), wrap the `record_boundary_correction` call:

```python
    span = _rally_det_span(conn, rally_id)
    # A hand-made half has no detector span, so there is no detector error
    # for this drag to measure -- the whole point of the corpus write. The
    # bounds edit itself still lands; only the label is skipped.
    if span is not None:
        record_boundary_correction(
            conn,
            rally_id=rally_id,
            source_id=span["source_id"],
            det_start_ms=span["det_start_ms"],
            det_end_ms=span["det_end_ms"],
            true_start_ms=body.start_ms,
            true_end_ms=body.end_ms,
        )
    set_bounds(conn, rally_id, body.start_ms, body.end_ms)
    return {"ok": True}
```

In `api_label` (starting `splitstep/api/routes.py:455`), refuse outright — a verdict is an explicit assertion the client should not be making here:

```python
    span = _rally_det_span(conn, rally_id)
    if span is None:
        raise HTTPException(
            status_code=400,
            detail="This rally has no detector span to judge — it was made by hand, "
                   "not proposed by the detector.",
        )
```

placed immediately after the existing `span = _rally_det_span(conn, rally_id)` line, replacing it.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_api_review.py tests/test_api_labels.py -q
```

Expected: PASS.

- [ ] **Step 7: Run the whole suite, lint, commit**

```bash
~/miniconda3/envs/splitstep/bin/pytest -q
~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
git add splitstep/api/routes.py tests/test_api_review.py tests/test_api_labels.py
git commit -m "feat(api): split and merge routes, and keep det-less rallies out of the corpus"
```

---

## Task 5: `web/src/lib/split.ts`

**Files:**
- Create: `web/src/lib/split.ts`
- Modify: `web/src/lib/types.ts`, `web/src/lib/api.ts`
- Test: `web/tests/split.test.ts` (create)

**Interfaces:**
- Consumes: `MIN_RALLY_MS` from `web/src/lib/timeline.ts`; `Rally` from `web/src/lib/types.ts`; the routes from Task 4.
- Produces:
  - `canSplit(rally: Rally, atMs: number): boolean`
  - `canMerge(rally: Rally, prev: Rally | undefined): boolean`
  - `applySplit(rallies: Rally[], rallyId: string, atMs: number, newId: string, sourceOrder?: string[]): Rally[]`
  - `applyMerge(rallies: Rally[], rallyId: string, sourceOrder?: string[]): Rally[]`
  - `api.splitRally(id: string, atMs: number): Promise<{ ok: boolean; new_rally_id: string }>`
  - `api.mergeRally(id: string): Promise<{ ok: boolean }>`

- [ ] **Step 1: Widen the `Rally` type**

In `web/src/lib/types.ts:37-38`:

```ts
  // NULL on a rally a human made by splitting one in two: the detector
  // never proposed it. See splitstep/db/migrations/009_rally_split.sql.
  // Every consumer that asks "is this a detector proposal?" tests this.
  det_start_ms: number | null
  det_end_ms: number | null
```

- [ ] **Step 2: Write the failing tests**

Create `web/tests/split.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { applyMerge, applySplit, canMerge, canSplit } from '../src/lib/split'
import type { Rally } from '../src/lib/types'

function rally(overrides: Partial<Rally> = {}): Rally {
  return {
    id: 'r1',
    session_id: 's1',
    source_id: 'src1',
    idx: 1,
    start_ms: 1000,
    end_ms: 9000,
    det_start_ms: 1000,
    det_end_ms: 9000,
    confidence: 0.8,
    starred: 0,
    rejected: 0,
    point: 0,
    reviewed_at: null,
    seen_at: null,
    note: '',
    ...overrides,
  }
}

describe('canSplit', () => {
  it('allows a cut with room for both halves', () => {
    expect(canSplit(rally(), 5000)).toBe(true)
  })

  it('refuses a cut that would leave a half under MIN_RALLY_MS', () => {
    // The client floor, which the server deliberately does not enforce --
    // it rejects only the incoherent (a zero-length half), leaving "merely
    // tiny" to be discouraged here, exactly as /bounds and clampMinGap
    // already divide the same question.
    expect(canSplit(rally(), 1050)).toBe(false)
    expect(canSplit(rally(), 8950)).toBe(false)
  })

  it('refuses a cut at or outside the boundaries', () => {
    expect(canSplit(rally(), 1000)).toBe(false)
    expect(canSplit(rally(), 9000)).toBe(false)
    expect(canSplit(rally(), 20000)).toBe(false)
  })
})

describe('canMerge', () => {
  const made = rally({ id: 'r2', idx: 2, start_ms: 5000, det_start_ms: null, det_end_ms: null })
  const prev = rally({ id: 'r1', idx: 1, start_ms: 1000, end_ms: 5000 })

  it('allows a hand-made half abutting its predecessor', () => {
    expect(canMerge(made, prev)).toBe(true)
  })

  it('refuses a rally carrying a detector span', () => {
    expect(canMerge(rally({ id: 'r2', start_ms: 5000 }), prev)).toBe(false)
  })

  it('refuses when there is no predecessor', () => {
    expect(canMerge(made, undefined)).toBe(false)
  })

  it('refuses a predecessor in another source', () => {
    expect(canMerge(made, rally({ source_id: 'src2', end_ms: 5000 }))).toBe(false)
  })

  it('refuses a predecessor that no longer abuts', () => {
    expect(canMerge(made, rally({ end_ms: 4000 }))).toBe(false)
  })
})

describe('applySplit', () => {
  it('replaces one rally with two abutting halves', () => {
    const out = applySplit([rally()], 'r1', 5000, 'new')
    expect(out).toHaveLength(2)
    expect([out[0].start_ms, out[0].end_ms]).toEqual([1000, 5000])
    expect([out[1].start_ms, out[1].end_ms]).toEqual([5000, 9000])
    expect(out[1].id).toBe('new')
  })

  it('gives the new half no detector span and leaves the first half its own', () => {
    const out = applySplit([rally()], 'r1', 5000, 'new')
    expect([out[0].det_start_ms, out[0].det_end_ms]).toEqual([1000, 9000])
    expect(out[1].det_start_ms).toBeNull()
    expect(out[1].det_end_ms).toBeNull()
  })

  it('inherits review flags and nulls nothing the server keeps', () => {
    const out = applySplit(
      [rally({ starred: 1, point: 1, note: 'late backhand', seen_at: 'T0' })],
      'r1', 5000, 'new',
    )
    expect(out[1].starred).toBe(1)
    expect(out[1].point).toBe(1)
    expect(out[1].note).toBe('late backhand')
    expect(out[1].seen_at).toBe('T0')
  })

  it('renumbers idx the way the server does — source order, then start_ms', () => {
    // The parity that matters. splitstep/db/rallies.py::_renumber orders by
    // sources.idx then start_ms; a client that renumbered differently would
    // print an idx the server disagrees with, on a field the reviewer reads
    // out loud.
    const rallies = [
      rally({ id: 'a1', source_id: 'src1', idx: 1, start_ms: 1000, end_ms: 9000 }),
      rally({ id: 'a2', source_id: 'src1', idx: 2, start_ms: 20000, end_ms: 25000 }),
      rally({ id: 'b1', source_id: 'src2', idx: 3, start_ms: 1000, end_ms: 4000 }),
    ]
    const sourceOrder = ['src1', 'src2']
    const out = applySplit(rallies, 'a1', 5000, 'new', sourceOrder)
    expect(out.map((r) => [r.id, r.idx])).toEqual([
      ['a1', 1], ['new', 2], ['a2', 3], ['b1', 4],
    ])
  })

  it('returns the list unchanged for an unknown id', () => {
    const input = [rally()]
    expect(applySplit(input, 'nope', 5000, 'new')).toEqual(input)
  })
})

describe('applyMerge', () => {
  it('absorbs the half into its predecessor and renumbers', () => {
    const rallies = applySplit([rally()], 'r1', 5000, 'new')
    const out = applyMerge(rallies, 'new')
    expect(out).toHaveLength(1)
    expect([out[0].id, out[0].start_ms, out[0].end_ms]).toEqual(['r1', 1000, 9000])
    expect(out[0].idx).toBe(1)
  })

  it('leaves the list alone when the merge is not allowed', () => {
    const input = [rally()]
    expect(applyMerge(input, 'r1')).toEqual(input)
  })
})
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd web && npx vitest run tests/split.test.ts
```

Expected: FAIL — cannot resolve `../src/lib/split`.

- [ ] **Step 4: Write the module**

Create `web/src/lib/split.ts`:

```ts
import { MIN_RALLY_MS } from './timeline'
import type { Rally } from './types'

/**
 * Splitting one rally into two, and putting the halves back.
 *
 * The detector merges two rallies whenever the break between them scores
 * above threshold. That is the correct trade -- the tuning that separates
 * them shreds real rallies -- but timeline mode could only ever move a
 * merged rally's two boundaries, and there was no third rally to move a
 * boundary to.
 *
 * A hand-made half carries `det_start_ms === null`. That is the whole
 * marker: it keeps the two halves from colliding in the label corpus (which
 * anchors on the detector's span), and it is what `canMerge` tests so an
 * undo can never destroy detector provenance.
 *
 * `applySplit`/`applyMerge` exist so TimelineMode can update in place rather
 * than triggering Session's `rallyRevision` remount. That is a UX
 * requirement, not an optimisation: a remount resets the playhead, so a cut
 * at 22.0s would throw the reviewer back to the top of the rally at exactly
 * the moment they want to trim the seam they just made.
 */

/**
 * Whether a cut at `atMs` leaves two rallies worth having.
 *
 * The MIN_RALLY_MS floor lives here and not on the server, which validates
 * only that both halves are non-empty. That is the same division /bounds
 * already draws with clampMinGap, and it is deliberate: media/concat.py
 * spells out that a hand-trimmed clip well under 1.5s is a real input, so
 * the floor is a nudge in the UI rather than a rule about what a library
 * may hold.
 */
export function canSplit(rally: Rally, atMs: number): boolean {
  return atMs - rally.start_ms >= MIN_RALLY_MS && rally.end_ms - atMs >= MIN_RALLY_MS
}

/**
 * Whether `rally` can be absorbed back into `prev`.
 *
 * Mirrors splitstep/db/rallies.py::merge_into_previous exactly, so the key
 * hint can be greyed before the request rather than after a 400. The det
 * test is the load-bearing one: merge may only ever undo something a human
 * made, never delete a row rally_labels is anchored to.
 */
export function canMerge(rally: Rally, prev: Rally | undefined): boolean {
  if (rally.det_start_ms !== null) return false
  if (!prev) return false
  if (prev.source_id !== rally.source_id) return false
  // Exact abutment. Once the seam has been trimmed the two rows no longer
  // describe one contiguous stretch of footage, and rejoining them would
  // invent play across the gap the reviewer just opened.
  return prev.end_ms === rally.start_ms
}

/**
 * Renumber `idx` the way splitstep/db/rallies.py::_renumber does: ordered by
 * the source's own idx, then by start_ms, assigned 1..N across the whole
 * session.
 *
 * Two implementations of one rule is a real cost. The alternative is a
 * remount on every cut (see the module comment), and the rule is frozen by
 * migration 001's UNIQUE(session_id, idx). web/tests/split.test.ts pins the
 * parity.
 *
 * `sourceOrder` is the session's source ids in `sources.idx` order. When
 * omitted, first appearance in `rallies` is used -- correct for the
 * single-source case and for any list already in server order.
 */
function renumber(rallies: Rally[], sourceOrder?: string[]): Rally[] {
  const order = sourceOrder ?? [...new Set(rallies.map((r) => r.source_id))]
  const rank = new Map(order.map((id, i) => [id, i]))
  return [...rallies]
    .sort((a, b) => {
      const bySource = (rank.get(a.source_id) ?? 0) - (rank.get(b.source_id) ?? 0)
      return bySource !== 0 ? bySource : a.start_ms - b.start_ms
    })
    .map((r, i) => ({ ...r, idx: i + 1 }))
}

/**
 * The local counterpart of split_rally. `newId` is the id the server
 * returned, not one invented here -- the two lists must agree on it or the
 * next merge would name a rally the server does not have.
 */
export function applySplit(
  rallies: Rally[],
  rallyId: string,
  atMs: number,
  newId: string,
  sourceOrder?: string[],
): Rally[] {
  const target = rallies.find((r) => r.id === rallyId)
  if (!target) return rallies
  const first: Rally = { ...target, end_ms: atMs }
  const second: Rally = {
    ...target,
    id: newId,
    start_ms: atMs,
    // No detector ever proposed this rally -- see the module comment.
    det_start_ms: null,
    det_end_ms: null,
  }
  const rest = rallies.filter((r) => r.id !== rallyId)
  return renumber([...rest, first, second], sourceOrder)
}

/** The local counterpart of merge_into_previous. */
export function applyMerge(
  rallies: Rally[],
  rallyId: string,
  sourceOrder?: string[],
): Rally[] {
  const ordered = renumber(rallies, sourceOrder)
  const i = ordered.findIndex((r) => r.id === rallyId)
  if (i === -1) return rallies
  const target = ordered[i]
  const prev = i > 0 ? ordered[i - 1] : undefined
  if (!canMerge(target, prev) || !prev) return rallies
  const merged: Rally = { ...prev, end_ms: target.end_ms }
  return renumber(
    ordered.filter((r) => r.id !== rallyId && r.id !== prev.id).concat(merged),
    sourceOrder,
  )
}
```

- [ ] **Step 5: Add the two API methods**

In `web/src/lib/api.ts`, after the `setBounds` entry:

```ts
  splitRally: (id: string, atMs: number) =>
    req<{ ok: boolean; new_rally_id: string }>(`/api/rallies/${id}/split`, {
      method: 'POST',
      body: JSON.stringify({ at_ms: atMs }),
    }),
  // `req` rather than `post`: post's return type has no new_rally_id, and
  // widening it would loosen every other rally write's shape for one caller.
  mergeRally: (id: string) => post(`/api/rallies/${id}/merge`),
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd web && npx vitest run tests/split.test.ts
```

Expected: PASS, all cases.

- [ ] **Step 7: Typecheck the widening**

```bash
cd web && npm run check
```

Expected: errors wherever `det_start_ms` is used as a bare `number`. Note each one — Task 8 fixes `resegment.ts` and `labels.ts`, which are the expected sites. If `npm run check` names any file outside those two plus `TimelineMode.svelte`, fix it in this task before committing.

- [ ] **Step 8: Commit**

```bash
git add web/src/lib/split.ts web/src/lib/types.ts web/src/lib/api.ts web/tests/split.test.ts
git commit -m "feat(web): pure split and merge, with server-parity renumbering"
```

---

## Task 6: Shortcuts

**Files:**
- Modify: `web/src/lib/shortcuts.ts`
- Test: `web/tests/shortcuts.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `TIMELINE` gains a `Split` group with `C` and `U`. `PRIMARY.timeline` becomes `[ ] C U Esc ?`.

- [ ] **Step 1: Write the failing test**

Append to `web/tests/shortcuts.test.ts`:

```ts
it('binds C and U in timeline mode', () => {
  const keys = shortcutKeys('timeline')
  expect(keys).toContain('C')
  expect(keys).toContain('U')
})

it('keeps the timeline strip at six and puts split in it', () => {
  // Past six the strip wraps and stops being glanceable, which is the
  // failure it replaces. The frame-step pair leaves for the `?` overlay --
  // they are a mirror pair, discoverable from one another.
  const strip = primaryShortcuts('timeline')
  expect(strip).toHaveLength(6)
  expect(strip.flatMap((s) => s.keys)).toEqual(['[', ']', 'C', 'U', 'Esc', '?'])
})
```

Ensure `shortcutKeys` and `primaryShortcuts` are imported at the top of that file.

- [ ] **Step 2: Run to verify it fails**

```bash
cd web && npx vitest run tests/shortcuts.test.ts
```

Expected: FAIL — `expected [ '[', ']', ',', '.', 'Esc', '?' ] to equal [ '[', ']', 'C', 'U', 'Esc', '?' ]`.

- [ ] **Step 3: Add the group**

In `web/src/lib/shortcuts.ts`, insert a `Split` group into `TIMELINE`, between `Trim` and `Playback`:

```ts
  {
    // `C` rather than `S` for "split": S is *star* in queue mode, the
    // reviewer crosses between the two modes constantly, and a reflex S in
    // timeline that cut instead of starred is the worst possible misfire for
    // a key whose inverse is conditional.
    title: 'Split',
    items: [
      { keys: ['C'], label: 'Split here into two rallies' },
      { keys: ['U'], label: 'Merge back into the previous' },
    ],
  },
```

- [ ] **Step 4: Update the strip**

Replace the `timeline` line in `PRIMARY`. Note every index below `Split` shifts by one — `Playback` is now `TIMELINE[2]` and `Leave` is `TIMELINE[3]`:

```ts
  timeline: [TIMELINE[0].items[0], TIMELINE[0].items[1], TIMELINE[1].items[0],
             TIMELINE[1].items[1], TIMELINE[3].items[0], TIMELINE[3].items[1]],
```

- [ ] **Step 5: Run to verify it passes**

```bash
cd web && npx vitest run tests/shortcuts.test.ts
```

Expected: PASS, including the pre-existing strip-is-a-subset-of-overlay and no-key-bound-twice checks.

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/shortcuts.ts web/tests/shortcuts.test.ts
git commit -m "feat(web): bind C to split and U to merge in timeline mode"
```

---

## Task 7: Wire `C` and `U` into TimelineMode

**Files:**
- Modify: `web/src/components/TimelineMode.svelte`

**Interfaces:**
- Consumes: `canSplit`, `canMerge`, `applySplit`, `applyMerge` from Task 5; `api.splitRally`, `api.mergeRally`; the existing `toaster`, `saveState`, `SAVED_NOTICE_MS`, `rallies`, `currentId`, `deck`.
- Produces: nothing consumed by later tasks.

This is the one task with no unit test — jsdom has no `<video>`, so TimelineMode is verified by hand, as every other mode is. The logic it calls is already covered by Task 5.

- [ ] **Step 1: Add the imports**

In the `<script>` block of `web/src/components/TimelineMode.svelte`:

```ts
  import { applyMerge, applySplit, canMerge, canSplit } from '../lib/split'
```

- [ ] **Step 2: Add the derived source order and the merge target**

After the existing `neighbours` derived block:

```ts
  // The session's sources in their own idx order -- what split.ts's
  // renumber needs to reproduce _renumber's ordering exactly. Derived from
  // `detail.sources` rather than from the rally list, which may not contain
  // a rally for every source.
  const sourceOrder = $derived(
    [...detail.sources].sort((a, b) => a.idx - b.idx).map((s) => s.id),
  )

  // The rally immediately before the current one in the same source, which
  // is what `U` would merge into. Computed here so the key hint can be
  // greyed before the request rather than after a 400.
  const mergePrev = $derived.by(() => {
    if (!rally) return undefined
    return rallies
      .filter((r) => r.source_id === rally.source_id && r.end_ms === rally.start_ms)
      .find((r) => r.id !== rally.id)
  })
```

- [ ] **Step 3: Add the two handlers**

After `applyEdit`:

```ts
  // Both handlers update `rallies` locally rather than asking Session to
  // remount: a remount resets the playhead (see the $effect on currentId),
  // which would throw the reviewer back to the top of the rally at exactly
  // the moment they want to trim the seam they just made. Session's
  // closeTimeline already refetches on Esc, so the queue sees both halves
  // with no wiring here.
  async function splitHere(): Promise<void> {
    if (!rally) return
    const atMs = Math.round(deck?.currentMs() ?? rally.start_ms)
    if (!canSplit(rally, atMs)) {
      toaster.push('The playhead is too close to a boundary to split here.')
      return
    }
    try {
      const { new_rally_id } = await api.splitRally(rally.id, atMs)
      rallies = applySplit(rallies, rally.id, atMs, new_rally_id, sourceOrder)
      saveState = 'saved'
      if (saveTimer !== undefined) clearTimeout(saveTimer)
      saveTimer = setTimeout(() => {
        saveState = 'idle'
        saveTimer = undefined
      }, SAVED_NOTICE_MS)
      toaster.push(`Split at ${formatTs(atMs)} — U to merge back`)
    } catch (e) {
      console.error('failed to split rally', e)
      saveState = 'error'
      toaster.push('Could not split this rally — check that the server is running.')
    }
  }

  async function mergeBack(): Promise<void> {
    if (!rally) return
    if (!canMerge(rally, mergePrev)) {
      // Two different refusals, and the reviewer's next move differs, so
      // they must not collapse into one sentence.
      toaster.push(
        rally.det_start_ms !== null
          ? 'This rally came from the detector — only a half you split can be merged back.'
          : 'Nothing abuts the start of this rally to merge it into.',
      )
      return
    }
    const merging = rally.id
    try {
      await api.mergeRally(merging)
      // Land on the survivor before the row disappears, or `rally` falls
      // back to rallies[0] and the reviewer is silently moved to the top of
      // the session.
      currentId = mergePrev!.id
      rallies = applyMerge(rallies, merging, sourceOrder)
      toaster.push('Merged back into the previous rally')
    } catch (e) {
      console.error('failed to merge rally', e)
      saveState = 'error'
      toaster.push('Could not merge this rally — check that the server is running.')
    }
  }
```

- [ ] **Step 4: Add the key cases**

In `onKey`'s `switch`, after the `']'` case:

```ts
      case 'c':
      case 'C':
        splitHere()
        break
      case 'u':
      case 'U':
        mergeBack()
        break
```

Both cases, upper and lower — matching how `r`/`R` and `u`/`U` are already written in LabelMode.

- [ ] **Step 5: Typecheck**

```bash
cd web && npm run check
```

Expected: no errors in `TimelineMode.svelte`. Errors may remain in `resegment.ts` and `labels.ts` — Task 8.

- [ ] **Step 6: Build, then verify by hand**

```bash
cd web && npm run build
```

Then, with `splitstep --library /Volumes/SanDisk_2TB/SplitStep serve` running, open a session, press `T` on a rally, park the playhead mid-rally and press `C`. Confirm: two boxes appear in the zoom band, the toast names the timecode, the playhead has **not** jumped, and `rally N` in the status line renumbers. Press `U`; confirm the halves rejoin and you are on the survivor. Press `U` again; confirm the refusal names the detector.

- [ ] **Step 7: Commit**

```bash
git add web/src/components/TimelineMode.svelte
git commit -m "feat(web): C splits the current rally, U merges the half back"
```

---

## Task 8: Fallout — re-segment counts and label mode

**Files:**
- Modify: `web/src/lib/resegment.ts`, `web/src/components/ResegmentPanel.svelte`, `web/src/lib/labels.ts`
- Test: `web/tests/resegment.test.ts`, `web/tests/labels.test.ts`

**Interfaces:**
- Consumes: the widened `Rally` type from Task 5.
- Produces: `splitCount(rallies: Rally[], sourceId: string): number`; `resegmentConfirmMessage(editedCount: number, splitCount: number): string`.

- [ ] **Step 1: Write the failing tests**

Append to `web/tests/resegment.test.ts`:

```ts
describe('splits versus boundary edits', () => {
  const detected = rally({ id: 'a', start_ms: 1000, end_ms: 5000,
                           det_start_ms: 1000, det_end_ms: 5000 })
  const dragged = rally({ id: 'b', start_ms: 1200, end_ms: 5000,
                          det_start_ms: 1000, det_end_ms: 5000 })
  const handMade = rally({ id: 'c', start_ms: 5000, end_ms: 9000,
                           det_start_ms: null, det_end_ms: null })

  it('counts hand-made rallies as splits, not as edited boundaries', () => {
    // `r.start_ms !== r.det_start_ms` is always true against a null det
    // span, so without the exclusion a split half would be counted here and
    // then described with the wrong noun -- "hand-edited boundaries" for a
    // rally whose boundaries were never edited.
    const all = [detected, dragged, handMade]
    expect(editedBoundaryCount(all, 'src1')).toBe(1)
    expect(splitCount(all, 'src1')).toBe(1)
  })

  it('names both losses, with correct singular and plural', () => {
    expect(resegmentConfirmMessage(1, 0)).toContain('1 hand-edited boundary')
    expect(resegmentConfirmMessage(0, 1)).toContain('1 split')
    expect(resegmentConfirmMessage(2, 3)).toContain('2 hand-edited boundaries')
    expect(resegmentConfirmMessage(2, 3)).toContain('3 splits')
  })

  it('says nothing about splits when there are none', () => {
    expect(resegmentConfirmMessage(2, 0)).not.toContain('split')
  })
})
```

Import `splitCount` at the top of that file.

Append to `web/tests/labels.test.ts`. Its `rally()` helper is **positional** —
`rally(idx: number, over: Partial<Rally> = {})` — unlike `resegment.test.ts`'s,
and already spreads `over`, so no change to it is needed:

```ts
it('leaves hand-made rallies out of the corpus queue entirely', () => {
  // A rally with no detector span has nothing to anchor a corpus row to.
  // Filtering at construction rather than skipping during next()/back() is
  // what keeps index and total truthful -- the "12 / 121" counter must not
  // promise judgements that can never be made.
  const detected = rally(1)
  const handMade = rally(2, { det_start_ms: null, det_end_ms: null })
  const c = new LabelController([detected, handMade], [])
  expect(c.total).toBe(1)
  expect(c.current?.id).toBe('r1')
})

it('lands on index 0 when asked to start at a hand-made rally', () => {
  // jumpTo already no-ops on an id it cannot find, so filtering at
  // construction needs no change there -- this pins that it stays true.
  const c = new LabelController(
    [rally(1), rally(2, { det_start_ms: null, det_end_ms: null })], [])
  c.jumpTo('r2')
  expect(c.current?.id).toBe('r1')
})

it('does not seed a verdict from a hand-made rally', () => {
  // The constructor's seeding loop must iterate the FILTERED list. A
  // hand-made half inherits its parent's bounds-derived span only by
  // accident; matching a stored record against it would attribute a verdict
  // to a clip nobody judged.
  const handMade = rally(1, { det_start_ms: null, det_end_ms: null })
  const c = new LabelController([handMade], [record({ verdict: 'clean' })])
  expect(c.total).toBe(0)
})
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd web && npx vitest run tests/resegment.test.ts tests/labels.test.ts
```

Expected: FAIL — `splitCount` is not exported; `resegmentConfirmMessage` takes one argument; `LabelController.total` is `2`.

- [ ] **Step 3: Update `resegment.ts`**

Replace the two functions in `web/src/lib/resegment.ts`:

```ts
/**
 * Rallies on `sourceId` whose live boundaries have diverged from what the
 * detector produced. Re-segmenting discards these -- the server carries
 * stars and rejections across by >50% overlap (see `replace_rallies` /
 * `STAR_OVERLAP_MIN` in splitstep/db/rallies.py), but a hand-dragged boundary
 * has no such carry-over, so the caller must name this count before paying
 * the cost (spec 6).
 *
 * Hand-made rallies are excluded and counted by `splitCount` instead. Their
 * det span is null, so `start_ms !== det_start_ms` is trivially true and they
 * would otherwise be counted here and then described with the wrong noun.
 */
export function editedBoundaryCount(rallies: Rally[], sourceId: string): number {
  return rallies.filter(
    (r) =>
      r.source_id === sourceId &&
      r.det_start_ms !== null &&
      (r.start_ms !== r.det_start_ms || r.end_ms !== r.det_end_ms),
  ).length
}

/**
 * Rallies on `sourceId` a human made by splitting one in two. Lost to a
 * re-segment for the same reason a boundary edit is, and for a reason the
 * word "boundary" does not cover: replace_rallies rebuilds from detector
 * intervals, and a hand-made rally has no interval to be rebuilt from.
 */
export function splitCount(rallies: Rally[], sourceId: string): number {
  return rallies.filter((r) => r.source_id === sourceId && r.det_start_ms === null).length
}

/** The confirmation copy naming that cost, singular/plural correct. */
export function resegmentConfirmMessage(editedCount: number, splits: number): string {
  const losses: string[] = []
  if (editedCount > 0) {
    losses.push(`${editedCount} hand-edited boundar${editedCount === 1 ? 'y' : 'ies'}`)
  }
  if (splits > 0) losses.push(`${splits} split${splits === 1 ? '' : 's'}`)
  // Both counts zero is still a real prompt: the reviewer is replacing every
  // rally on the source and should be told so, even when nothing hand-made
  // is at stake.
  const what = losses.length ? losses.join(' and ') : 'nothing hand-edited'
  return `Re-segmenting discards ${what} on this source. Stars and rejections are kept. Continue?`
}
```

- [ ] **Step 4: Update the caller**

In `web/src/components/ResegmentPanel.svelte`, three exact edits.

Line 6, the import:

```svelte
  import { editedBoundaryCount, resegmentConfirmMessage, splitCount } from '../lib/resegment'
```

After line 48 (`const editedCount = $derived(editedBoundaryCount(rallies, sourceId))`), add:

```svelte
  const splits = $derived(splitCount(rallies, sourceId))
```

Line 151:

```svelte
      const ok = confirm(resegmentConfirmMessage(editedCount, splits))
```

`editedCount` keeps its name — this task passes a second count, it does not rename anything.

- [ ] **Step 5: Filter in `LabelController`**

In `web/src/lib/labels.ts`, in the constructor at line 91-92, replace `this.#rallies = rallies` with:

```ts
    // Hand-made rallies (det_start_ms null -- a half someone split off, see
    // web/src/lib/split.ts) are dropped here, not skipped during
    // next()/back(). rally_labels anchors on the detector's own span, so
    // there is nothing for a judgement on one of these to attach to.
    // Filtering at construction is what keeps `index` and `total` truthful:
    // the counter must not promise judgements that can never be made. It is
    // also why `jumpTo` needs no change -- it already no-ops on an id it
    // cannot find, so a startAtRallyId naming a hand-made half lands on
    // index 0.
    //
    // Deliberately narrower than QueueController's filtering: a REJECTED
    // rally stays, because it is precisely the `not_play` the corpus is
    // short of (see below).
    this.#rallies = rallies.filter((r) => r.det_start_ms !== null)
```

and update the loop below it to iterate `this.#rallies` rather than the raw `rallies` parameter, so a hand-made rally cannot seed a verdict:

```ts
    for (const r of this.#rallies) {
```

- [ ] **Step 6: Run to verify they pass**

```bash
cd web && npx vitest run
```

Expected: PASS, every test file.

- [ ] **Step 7: Typecheck and build**

```bash
cd web && npm run check && npm run build
```

Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add web/src/lib/resegment.ts web/src/lib/labels.ts web/src/components/ResegmentPanel.svelte web/tests/resegment.test.ts web/tests/labels.test.ts
git commit -m "feat(web): name splits as their own re-segment cost, and keep them out of label mode"
```

---

## Task 9: Document the det-less rally

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing.

- [ ] **Step 1: Add the convention**

In `CLAUDE.md`, in the "Conventions that matter" list, after the `rally_labels` bullet:

```markdown
- **A rally with `det_start_ms IS NULL` was made by a human, not proposed by
  the detector.** Timeline mode's `C` cuts one rally in two; the second half
  carries no detector span, because `rally_labels` anchors on
  `(source_id, det_start_ms, det_end_ms)` and two halves inheriting one span
  would collide in the corpus — the second labelled would silently overwrite
  the first. Giving each half its own span is worse: `det_*` records what the
  detector *originally guessed*, so spans it never produced are fabricated
  training data. The absence is the marker rather than a boolean beside it,
  since a boolean can drift out of agreement with the columns it describes.
  Consequences, all of them load-bearing: `merge_into_previous` (`U`) refuses
  any rally that has a det span, so an undo can never delete a row the corpus
  is anchored to; `/bounds` skips its corpus write and `/label` refuses
  outright; `LabelController` filters these rallies out in its constructor so
  `index`/`total` stay truthful; and `editedBoundaryCount` must exclude them
  or count them as boundary edits they are not. A re-segment destroys them,
  like every other manual edit — `replace_rallies` rebuilds from detector
  intervals and a hand-made rally has none.
```

- [ ] **Step 2: Update the frontend module list**

In the "Frontend" section, add `split.ts` to the sentence listing `lib/` modules:

```markdown
plus `shortcuts.ts` (the one place a keybinding is written down; the inline
strip and the `?` overlay both render from it), `status.ts` (status → label +
tone for the list cards), `jobs.ts` (job phase names and batch elapsed),
`split.ts` (cutting a rally in two and putting it back, with the same idx
ordering `_renumber` uses), `flash.ts` (the verdict confirmation) and
`errors.ts` (`ApiError` → a sentence)
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: a rally with no detector span is one a human made"
```

---

## Final verification

- [ ] **Run everything**

```bash
~/miniconda3/envs/splitstep/bin/pytest -q
~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
cd web && npx vitest run && npm run check && npm run build
```

Expected: all pass. Python was 680 tests before this plan; it should now be roughly 700.

- [ ] **Hand-verify the whole loop**

With `serve` running: split a rally, trim the seam with `[`, star one half, press `Esc`, confirm the queue shows both halves with the star on the right one and a truthful `N / M` count. Enter label mode with `L` and confirm the total dropped by the number of hand-made halves. Open the re-segment panel and confirm the confirmation names the split count.

- [ ] **Merge the branch**

```bash
git checkout master && git merge --ff-only rally-split
```
