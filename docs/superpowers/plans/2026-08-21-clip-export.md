# Clip Export Implementation Plan (Plan A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `point` review flag and cut starred/point rallies to 4K clips on disk, at a profile locked forever so reels can later concat them with `-c copy`.

**Architecture:** A third boolean on `rallies` beside `starred`/`rejected`, carried across re-segments by overlap. A `clip` job encodes one span to `clips/<idx>-<start>-<end>.mp4` at a fixed profile with rotation baked in. Span-derived filenames make export incremental by construction — a clip either exists at the path its bounds imply, or it does not.

**Tech Stack:** Python 3.12 (sqlite3, FastAPI, ffmpeg/ffprobe, pytest), Svelte 5 runes + TypeScript + vitest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-21-clip-export-and-reels-design.md`. Read §3 and §4 before Task 1. This plan covers those two sections only; §5–§6 (reels, builder, preview) are Plan B.
- Python runs from the `bootleg` conda env by path: `~/miniconda3/envs/bootleg/bin/pytest`, `~/miniconda3/envs/bootleg/bin/ruff`.
- ruff line-length 100. **ruff 0.16.3 defaults here are broader than the classic set** — `I001` (import ordering), `BLE001` (blind except), `PLW1510` (`subprocess.run` without `check=`) are active.
- `pytest` runs with `filterwarnings = ["error"]` — a new warning fails the suite.
- Migrations are numbered `.sql` applied by `PRAGMA user_version`. Add `005_point_flag.sql`; **never edit `001`–`003`**, all of which are applied to the user's real library.
- **The locked clip profile may never change** once a clip exists: `mp4 · H.264 High · yuv420p · 3840×2160 · 30 fps CFR · CRF 20 · AAC 128k 48 kHz stereo`. Changing it breaks `-c copy` against every clip ever cut.
- **Rotation never comes from the file's display matrix.** Pass `-noautorotate`, apply `rotation_filter(rotation_deg)`, and strip stale side data with `-display_rotation 0` exactly as `make_proxy` does.
- Comments explain **why**, not what. This codebase carries long rationale comments on non-obvious calls; match that density.
- API routes are `def`, not `async def`.
- All frontend logic lives in `web/src/lib/`, never in a `.svelte` file — jsdom has no `<video>`.
- ffmpeg must be on PATH. YOLO is never run in tests; nothing here touches detection.

---

### Task 1: The `point` flag in the database

**Files:**
- Create: `bootleg/db/migrations/005_point_flag.sql`
- Modify: `bootleg/db/rallies.py` (add `set_point`; `replace_rallies` carries `point`)
- Test: `tests/test_db.py` (append), `tests/test_rallies_point.py` (create)

**Interfaces:**
- Consumes: `bootleg.db.schema.migrate`, the existing `replace_rallies` / `_overlaps_any`.
- Produces: `set_point(conn, rally_id: str, point: bool) -> None`; `rallies.point` column.

- [ ] **Step 1: Write the migration**

Create `bootleg/db/migrations/005_point_flag.sql`:

```sql
-- A third review flag beside starred and rejected.
--
--   rejected  not a rally at all -- a bad detection
--   point     a point was played out, whatever the quality
--   starred   a highlight worth showing
--
-- The three are independent booleans. starred is expected to be a subset of
-- point in practice but is deliberately not constrained to be: a warm-up rally
-- can be worth watching without being a point, and enforcing containment would
-- make the reviewer argue with the tool.
ALTER TABLE rallies ADD COLUMN point INTEGER NOT NULL DEFAULT 0;

-- One-time reinterpretation of existing data, and it is a reinterpretation
-- rather than a copy. Until now a star meant "a point was played out" -- the
-- first real session starred all 24 points of a filmed tiebreaker, double
-- faults and missed returns included, because star was the only mark available
-- and "this is a point" was the thing worth recording. Moving that meaning to
-- `point` leaves `starred` free to mean "a highlight I like", which is what it
-- was always supposed to mean.
--
-- Stars are cleared rather than left set. If the second pass never happens, an
-- empty highlight set is honest and a set that still silently means "point" is
-- not. PRAGMA user_version guarantees this runs exactly once.
UPDATE rallies SET point = 1 WHERE starred = 1;
UPDATE rallies SET starred = 0;

CREATE INDEX idx_rallies_point ON rallies(point) WHERE point = 1;
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_rallies_point.py`:

```python
import pytest

from bootleg.db.rallies import list_rallies, replace_rallies, set_point, set_star
from bootleg.db.sessions import add_source, find_or_create_session_for_date
from bootleg.detect.segment import Interval


@pytest.fixture
def seeded(conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, _idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    return {"session_id": session_id, "source_id": source_id}


def _rallies(conn, session_id):
    return list_rallies(conn, session_id)


def test_point_defaults_to_zero(conn, seeded):
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    assert _rallies(conn, seeded["session_id"])[0]["point"] == 0


def test_set_point_toggles_and_stamps_reviewed_at(conn, seeded):
    # reviewed_at records that a human has ruled on this rally at all.
    # Marking a point is such a ruling -- otherwise a reviewer who marks every
    # point of a tiebreaker and stars none ends with a session that still reads
    # unreviewed.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rallies(conn, seeded["session_id"])[0]["id"]

    set_point(conn, rally_id, True)
    row = _rallies(conn, seeded["session_id"])[0]
    assert row["point"] == 1
    assert row["reviewed_at"] is not None

    set_point(conn, rally_id, False)
    assert _rallies(conn, seeded["session_id"])[0]["point"] == 0


def test_set_point_does_not_move_reviewed_at_once_set(conn, seeded):
    # COALESCE, matching set_star/set_rejected: the first ruling is the one
    # that counts, so re-marking does not rewrite when review happened.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rallies(conn, seeded["session_id"])[0]["id"]
    set_point(conn, rally_id, True)
    first = _rallies(conn, seeded["session_id"])[0]["reviewed_at"]
    set_point(conn, rally_id, False)
    assert _rallies(conn, seeded["session_id"])[0]["reviewed_at"] == first


def test_point_is_independent_of_starred(conn, seeded):
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rallies(conn, seeded["session_id"])[0]["id"]
    set_point(conn, rally_id, True)
    set_star(conn, rally_id, True)
    row = _rallies(conn, seeded["session_id"])[0]
    assert (row["point"], row["starred"]) == (1, 1)


def test_replace_rallies_carries_point_across_a_resegment(conn, seeded):
    """The test that stops a threshold sweep wiping every point mark.

    replace_rallies deletes and rebuilds every rally for a source. starred and
    rejected have always been carried across by >50% overlap; point must be
    too, or the first re-segment silently discards the reviewer's entire
    tiebreaker.
    """
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rallies(conn, seeded["session_id"])[0]["id"]
    set_point(conn, rally_id, True)

    # A sweep that shifts the boundaries slightly -- still clearly the same rally.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1200, 4800, 0.7)])

    assert _rallies(conn, seeded["session_id"])[0]["point"] == 1


def test_a_new_rally_that_overlaps_nothing_is_not_a_point(conn, seeded):
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rallies(conn, seeded["session_id"])[0]["id"]
    set_point(conn, rally_id, True)

    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1200, 4800, 0.7), Interval(60_000, 66_000, 0.6)])
    rows = _rallies(conn, seeded["session_id"])
    assert [r["point"] for r in rows] == [1, 0]


def test_a_rejected_carry_over_does_not_gain_a_point(conn, seeded):
    # rejected takes priority over starred in the existing carry-over; point is
    # orthogonal to both and must not be invented for a segment that only ever
    # overlapped a rejection.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    from bootleg.db.rallies import set_rejected
    set_rejected(conn, _rallies(conn, seeded["session_id"])[0]["id"], True)
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1200, 4800, 0.7)])
    row = _rallies(conn, seeded["session_id"])[0]
    assert (row["rejected"], row["point"]) == (1, 0)
```

Append to `tests/test_db.py` — extend the existing table-set assertion in `test_migrate_creates_all_tables` is not needed (no new table), but the version assertion must move:

```python
# In test_migrate_is_idempotent, the expected version becomes 4.
```

Find that assertion (it currently expects `3`) and update it to `4`. Do not add a new test for it.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_rallies_point.py -q`
Expected: FAIL — `ImportError: cannot import name 'set_point'`

- [ ] **Step 4: Add `set_point` and carry `point` in `replace_rallies`**

In `bootleg/db/rallies.py`, add beside `set_star`:

```python
def set_point(conn: sqlite3.Connection, rally_id: str, point: bool) -> None:
    """Mark (or unmark) a rally as a point that was played out.

    Stamps reviewed_at through the same COALESCE set_star/set_rejected use.
    All three flags are rulings on the clip, and reviewed_at records that a
    human has ruled on a rally at all -- so a reviewer who marks every point
    of a tiebreaker and stars none must still end with a reviewed session.
    """
    conn.execute(
        "UPDATE rallies SET point = ?, reviewed_at = COALESCE(reviewed_at, ?)"
        " WHERE id = ?",
        (int(point), _now(), rally_id),
    )
    conn.commit()
```

In `replace_rallies`, widen the read-back and the insert. The `SELECT` becomes:

```python
        old = conn.execute(
            "SELECT start_ms, end_ms, starred, rejected, point FROM rallies"
            " WHERE source_id = ? AND (starred = 1 OR rejected = 1 OR point = 1)",
            (source_id,),
        ).fetchall()
```

and inside the loop, after the existing `starred`/`rejected` lines:

```python
            # Carried independently of starred/rejected: `point` answers "was a
            # point played out here", which is orthogonal to whether the clip
            # is a highlight or a bad detection. Without this line the first
            # threshold sweep silently discards the reviewer's whole
            # tiebreaker -- the same class of loss the star carry-over exists
            # to prevent.
            point = _overlaps_any(iv, old, "point")
```

and the INSERT gains the column:

```python
            conn.execute(
                "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
                "det_start_ms,det_end_ms,confidence,starred,rejected,point)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, session_id, source_id, -placeholder_idx, iv.start_ms,
                 iv.end_ms, iv.start_ms, iv.end_ms, iv.confidence,
                 int(starred), int(rejected), int(point)),
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_rallies_point.py -q`
Expected: PASS, 7 passed

- [ ] **Step 6: Run the full suite and the linter**

Run: `~/miniconda3/envs/bootleg/bin/pytest -q && ~/miniconda3/envs/bootleg/bin/ruff check bootleg tests`
Expected: all pass. `tests/test_db.py`, `tests/test_api.py` and `tests/test_labels.py` all exercise `replace_rallies`; they must still pass unchanged apart from the version bump.

- [ ] **Step 7: Commit**

```bash
git add bootleg/db/migrations/005_point_flag.sql bootleg/db/rallies.py \
        tests/test_rallies_point.py tests/test_db.py
git commit -m "feat(db): a point flag, and a reinterpretation of every star

Star was doing two jobs. Reviewing a filmed tiebreaker, every point got
starred -- double faults and missed returns included -- because star was the
only mark available and 'this is a point' was the thing worth recording,
which left no way to say 'this one was good'.

004 moves that meaning to \`point\` and clears the stars: if the second pass
never happens, an empty highlight set is honest and one that silently still
means 'point' is not.

replace_rallies carries point across by overlap like starred and rejected.
Without that the first threshold sweep discards the whole tiebreaker, and
test_replace_rallies_carries_point_across_a_resegment is what catches it."
```

---

### Task 2: The point API route

**Files:**
- Modify: `bootleg/api/routes.py` (body model near the other flag bodies; route beside `api_star`)
- Test: `tests/test_api_review.py` (append)

**Interfaces:**
- Consumes: `set_point` (Task 1), the existing `_session_id_for_rally`, `refresh_session_review_status`.
- Produces: `POST /api/rallies/{rally_id}/point` — body `{"point": bool}` → `{"ok": true, "session_status": str}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_review.py` (reuse its existing `client` and `seeded` fixtures; check their names before writing and match them):

```python
def test_point_route_sets_the_flag(client, conn, seeded):
    rally_id = conn.execute("SELECT id FROM rallies ORDER BY idx").fetchone()["id"]
    r = client.post(f"/api/rallies/{rally_id}/point", json={"point": True})
    assert r.status_code == 200
    assert conn.execute(
        "SELECT point FROM rallies WHERE id = ?", (rally_id,)
    ).fetchone()["point"] == 1


def test_point_route_reports_session_status(client, conn, seeded):
    rally_id = conn.execute("SELECT id FROM rallies ORDER BY idx").fetchone()["id"]
    r = client.post(f"/api/rallies/{rally_id}/point", json={"point": True})
    assert "session_status" in r.json()


def test_point_route_404s_on_an_unknown_rally(client, seeded):
    r = client.post("/api/rallies/nope/point", json={"point": True})
    assert r.status_code == 404


def test_point_does_not_touch_starred(client, conn, seeded):
    rally_id = conn.execute("SELECT id FROM rallies ORDER BY idx").fetchone()["id"]
    client.post(f"/api/rallies/{rally_id}/star", json={"starred": True})
    client.post(f"/api/rallies/{rally_id}/point", json={"point": True})
    row = conn.execute(
        "SELECT starred, point FROM rallies WHERE id = ?", (rally_id,)
    ).fetchone()
    assert (row["starred"], row["point"]) == (1, 1)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_api_review.py -q -k point`
Expected: FAIL — 404/405 on an unregistered route.

- [ ] **Step 3: Add the body model and the route**

In `bootleg/api/routes.py`, add beside `RejectBody`:

```python
class PointBody(BaseModel):
    point: bool
```

Add `set_point` to the `bootleg.db.rallies` import list, and add the route beside `api_reject`:

```python
@router.post("/api/rallies/{rally_id}/point")
def api_point(rally_id: str, body: PointBody, request: Request):
    conn = _conn(request)
    session_id = _session_id_for_rally(conn, rally_id)
    set_point(conn, rally_id, body.point)
    # Refreshes review status, unlike the label route: marking a point is a
    # ruling on the clip in the same family as star and reject, and
    # reviewed_at records that a human ruled on the rally at all.
    return {"ok": True, "session_status": refresh_session_review_status(conn, session_id)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_api_review.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite and the linter**

Run: `~/miniconda3/envs/bootleg/bin/pytest -q && ~/miniconda3/envs/bootleg/bin/ruff check bootleg tests`
Expected: all pass, ruff clean

- [ ] **Step 6: Commit**

```bash
git add bootleg/api/routes.py tests/test_api_review.py
git commit -m "feat(api): mark a rally as a point

Refreshes session review status, unlike the label route -- a point is a
ruling on the clip in the same family as star and reject, not a note about
the detector."
```

---

### Task 3: `P` in the review queue

**Files:**
- Modify: `web/src/lib/types.ts` (`Rally.point`)
- Modify: `web/src/lib/api.ts` (`point`)
- Modify: `web/src/lib/queue.ts` (`point()`, `currentIsPoint`, `pointCount`, action kind)
- Modify: `web/src/lib/persist.ts` (persist the new action kind)
- Modify: `web/src/components/QueueMode.svelte` (key, indicator, help line)
- Test: `web/tests/queue.test.ts` (append), `web/tests/persist.test.ts` (append)

**Interfaces:**
- Consumes: `QueueController`'s existing `#record`/`revert` machinery.
- Produces: `QueueController.point(): PersistableAction | null`, `currentIsPoint`, `pointCount`; `PersistableAction.kind` gains `'point'`; `PersistApi` gains `point(id, point)`.

- [ ] **Step 1: Add the type and the API method**

In `web/src/lib/types.ts`, add to `Rally`:

```ts
  point: number
```

In `web/src/lib/api.ts`, beside `reject`:

```ts
  point: (id: string, point: boolean) => post(`/api/rallies/${id}/point`, { point }),
```

and add `point` to `PersistApi` in `web/src/lib/persist.ts`:

```ts
  point: (id: string, point: boolean) => Promise<unknown>
```

- [ ] **Step 2: Write the failing tests**

Append to `web/tests/queue.test.ts` (its `rally()` helper needs `point: 0` added to the defaults):

```ts
  it('point() toggles and does not advance', () => {
    const action = q.point()
    expect(action?.kind).toBe('point')
    expect(action?.point).toBe(true)
    expect(q.index).toBe(0)
    expect(q.currentIsPoint).toBe(true)

    expect(q.point()?.point).toBe(false)
    expect(q.currentIsPoint).toBe(false)
  })

  it('point is independent of star', () => {
    // A highlight is a subset of points in practice but not by construction:
    // a warm-up rally can be worth watching without being a point.
    q.point()
    q.star()
    expect(q.currentIsPoint).toBe(true)
    expect(q.currentIsStarred).toBe(true)
  })

  it('seeds pointCount from the server snapshot', () => {
    const seeded = new QueueController([rally(1, { point: 1 }), rally(2)])
    expect(seeded.pointCount).toBe(1)
  })

  it('undo restores the previous point state', () => {
    q.point()
    q.undo()
    expect(q.currentIsPoint).toBe(false)
  })

  it('revert restores the failed action rallys point without moving the cursor', () => {
    const action = q.point()!
    q.skip()
    q.revert(action)
    expect(q.index).toBe(1)
    q.back()
    expect(q.currentIsPoint).toBe(false)
  })

  it('skip carries point through untouched', () => {
    // Same reasoning as starred/rejected: the right arrow is the only way
    // forward, so it lands on rallies the user has just flagged and must not
    // silently clear one.
    q.point()
    const action = q.skip()
    expect(action?.point).toBe(true)
  })
```

Append to `web/tests/persist.test.ts`:

```ts
  it('persists a point action', async () => {
    const calls: unknown[] = []
    const api = { ...noopApi, point: async (id: string, p: boolean) => { calls.push([id, p]); return {} } }
    const outcome = await persistAction(
      { kind: 'point', rallyId: 'r1', starred: false, rejected: false, point: true,
        previousStarred: false, previousRejected: false, previousPoint: false },
      api,
    )
    expect(outcome).toEqual({ ok: true })
    expect(calls).toEqual([['r1', true]])
  })
```

Match the existing file's fixture names; if it has no `noopApi`, build the fake inline the way its other tests do.

- [ ] **Step 3: Run the tests to verify they fail**

Run (from `web/`): `npx vitest run tests/queue.test.ts tests/persist.test.ts`
Expected: FAIL — `q.point is not a function`

- [ ] **Step 4: Extend `QueueController`**

In `web/src/lib/queue.ts`, widen the action types:

```ts
export interface PersistableAction {
  kind: 'star' | 'reject' | 'skip' | 'point'
  rallyId: string
  starred: boolean
  rejected: boolean
  point: boolean
  previousStarred: boolean
  previousRejected: boolean
  previousPoint: boolean
}
```

`UndoAction` gains `point: boolean` likewise, and `HistoryEntry` gains `point: boolean`.

Add a `#points` Set alongside `#starred`/`#rejected`, seed it in the constructor from `r.point`, and add:

```ts
  isPoint(rallyId: string): boolean {
    return this.#points.has(rallyId)
  }

  get currentIsPoint(): boolean {
    const r = this.current
    return r ? this.#points.has(r.id) : false
  }

  get pointCount(): number {
    return this.#points.size
  }

  point(): PersistableAction | null {
    const r = this.current
    if (!r) return null
    this.#record()
    const previousPoint = this.#points.has(r.id)
    const nowPoint = !previousPoint
    if (nowPoint) this.#points.add(r.id)
    else this.#points.delete(r.id)
    // Deliberately does NOT advance, and deliberately does not touch
    // starred/rejected: "was a point played out" is orthogonal to "is this a
    // highlight" and to "is this a rally at all".
    return {
      kind: 'point',
      rallyId: r.id,
      starred: this.#starred.has(r.id),
      rejected: this.#rejected.has(r.id),
      point: nowPoint,
      previousStarred: this.#starred.has(r.id),
      previousRejected: this.#rejected.has(r.id),
      previousPoint,
    }
  }
```

Every existing action constructor (`star`, `reject`, `skip`, `undo`) must be widened to carry `point`/`previousPoint`, `#record()` must snapshot it, `undo()` must restore it, `revert()` must restore it, and `liveSnapshot()` must project it. Follow exactly the pattern each already uses for `starred`.

- [ ] **Step 5: Persist it**

In `web/src/lib/persist.ts`, add the case:

```ts
      case 'point':
        await api.point(action.rallyId, action.point)
        break
```

and in the `'undo'` case, re-sync point alongside star and reject — undo can restore any of the three, so all three go back to the server, sequentially for the same reason the existing two are sequential.

- [ ] **Step 6: Wire the key and the indicator**

In `web/src/components/QueueMode.svelte`, add to `onKey`:

```ts
      case 'p':
      case 'P':
        apply(queue.point())
        break
```

Add a `currentPoint` derived value beside `currentStarred` (reading `version`, then `queue.currentIsPoint` — never `current.point`, for the reason the existing comment gives), render an indicator beside the star, add `pointCount` to the stats line, and extend the help line:

```
S star · P point · X reject (again to undo) · R replay · ← back · → next · U undo · 1/2/3 speed · T timeline · L label
```

- [ ] **Step 7: Run the tests and the type check**

Run (from `web/`): `npx vitest run && npm run check`
Expected: all pass, svelte-check 0 errors 0 warnings. Every test fixture that builds a `Rally` needs `point: 0` — there are several across `web/tests/`; find them all with a failing type check rather than by guessing.

- [ ] **Step 8: Commit**

```bash
git add web/src/lib/types.ts web/src/lib/api.ts web/src/lib/queue.ts \
        web/src/lib/persist.ts web/src/components/QueueMode.svelte web/tests
git commit -m "feat(web): P marks a rally as a point

A third flag beside star and reject, orthogonal to both: 'was a point played
out' is a different question from 'is this a highlight' and from 'is this a
rally at all'. Does not advance, for the same reason star and reject stopped
advancing."
```

---

### Task 4: The locked clip profile

**Files:**
- Modify: `bootleg/media/transcode.py` (add `make_clip`)
- Create: `bootleg/media/clips.py` (`clip_relpath`)
- Test: `tests/test_clips.py`

**Interfaces:**
- Consumes: `run_ffmpeg`, `rotation_filter`, `probe`.
- Produces:
  - `CLIP_WIDTH: int`, `CLIP_HEIGHT: int`, `CLIP_FPS: int`, `CLIP_CRF: int`
  - `make_clip(src: Path, dst: Path, *, start_ms: int, end_ms: int, rotation_deg: int = 0) -> None`
  - `clip_relpath(source_idx: int, start_ms: int, end_ms: int) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_clips.py`:

```python
import subprocess

import pytest

from bootleg.media.clips import clip_relpath
from bootleg.media.probe import probe
from bootleg.media.transcode import CLIP_FPS, CLIP_HEIGHT, CLIP_WIDTH, make_clip


@pytest.fixture
def source_4k(tmp_path):
    """6 seconds of 4K30 with a tone, so a 2-second cut has room either side."""
    out = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=3840x2160:rate=30:duration=6",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(out)],
        check=True, capture_output=True,
    )
    return out


def test_clip_relpath_is_derived_from_the_span(tmp_path):
    # Span-derived, never idx-derived: _renumber reassigns rallies.idx across a
    # whole session on every replace_rallies, so a name built from idx points
    # at a different rally after any threshold sweep.
    assert clip_relpath(1, 738500, 745500) == "01-738500-745500.mp4"
    assert clip_relpath(12, 0, 100) == "12-0-100.mp4"


def test_clip_relpath_is_stable_for_the_same_span(tmp_path):
    assert clip_relpath(1, 1000, 5000) == clip_relpath(1, 1000, 5000)


def test_make_clip_hits_the_locked_profile(source_4k, tmp_path):
    """The most important test in this plan.

    Encode-profile drift is the one silent failure that breaks `-c copy`
    against every clip ever cut, and it would not surface until a reel
    rendered wrong -- long after the clips were made.
    """
    dst = tmp_path / "clip.mp4"
    make_clip(source_4k, dst, start_ms=1000, end_ms=3000)
    info = probe(dst)
    assert (info.width, info.height) == (CLIP_WIDTH, CLIP_HEIGHT)
    assert info.codec_name == "h264"
    assert round(info.fps) == CLIP_FPS
    assert info.has_audio
    assert 1900 <= info.duration_ms <= 2100


def test_make_clip_uses_high_profile_and_yuv420p(source_4k, tmp_path):
    # probe() does not expose these, so read them directly -- they are part of
    # the locked profile and a mismatch breaks concat.
    dst = tmp_path / "clip.mp4"
    make_clip(source_4k, dst, start_ms=1000, end_ms=3000)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=profile,pix_fmt", "-of", "csv=p=0", str(dst)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert "High" in out
    assert "yuv420p" in out


def test_make_clip_audio_is_aac_48k_stereo(source_4k, tmp_path):
    dst = tmp_path / "clip.mp4"
    make_clip(source_4k, dst, start_ms=1000, end_ms=3000)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name,sample_rate,channels",
         "-of", "csv=p=0", str(dst)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert out.startswith("aac")
    assert "48000" in out
    assert out.endswith("2")


def test_make_clip_bakes_rotation_rather_than_flagging_it(source_4k, tmp_path):
    """A rotation left as a container flag would concat into a reel that flips
    halfway through, and rotation-aware players would double-rotate it."""
    dst = tmp_path / "clip.mp4"
    make_clip(source_4k, dst, start_ms=1000, end_ms=3000, rotation_deg=180)
    info = probe(dst)
    assert info.rotation_deg == 0
    # 180 does not swap the axes, so the locked frame size is unchanged.
    assert (info.width, info.height) == (CLIP_WIDTH, CLIP_HEIGHT)


def test_make_clip_conforms_a_quarter_turn_to_the_locked_frame(tmp_path):
    """At 90 the source's axes swap, so rotation must be applied BEFORE scale
    and pad -- scaling first pads against the wrong axis."""
    src = tmp_path / "portrait.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=2160x3840:rate=30:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True,
    )
    dst = tmp_path / "clip.mp4"
    make_clip(src, dst, start_ms=0, end_ms=2000, rotation_deg=90)
    info = probe(dst)
    assert (info.width, info.height) == (CLIP_WIDTH, CLIP_HEIGHT)


def test_make_clip_upscales_and_pads_a_1080p_source(tmp_path):
    """A reclaimed source is cut from the 1080p proxy. The clip library cannot
    hold mixed parameters -- `-c copy` refuses them -- so it is conformed."""
    src = tmp_path / "small.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1920x1080:rate=30:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True,
    )
    dst = tmp_path / "clip.mp4"
    make_clip(src, dst, start_ms=0, end_ms=2000)
    info = probe(dst)
    assert (info.width, info.height) == (CLIP_WIDTH, CLIP_HEIGHT)


def test_make_clip_cuts_from_the_requested_in_point(source_4k, tmp_path):
    """Cut accuracy is not optional: an in-point landing on the previous
    keyframe would put a second of the wrong footage at the head of a clip,
    and these boundaries were trimmed by hand."""
    dst = tmp_path / "clip.mp4"
    make_clip(source_4k, dst, start_ms=2000, end_ms=4000)
    assert 1900 <= probe(dst).duration_ms <= 2100
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_clips.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'bootleg.media.clips'`

- [ ] **Step 3: Write `bootleg/media/clips.py`**

```python
def clip_relpath(source_idx: int, start_ms: int, end_ms: int) -> str:
    """A clip's filename within its session's `clips/` directory.

    Derived from the span, never from `rallies.idx`: `_renumber` reassigns idx
    across a whole session on every `replace_rallies`, so a name built from it
    silently points at a different rally after any threshold sweep.

    Span-derived names buy three things. They survive re-segmentation; they are
    self-identifying on disk, so an orphan can be read rather than guessed at;
    and they make export incremental by construction -- a clip either exists at
    the path its bounds imply, or it does not. There is no separate staleness
    record to keep in sync, which is why this is a pure function and not a
    database column.
    """
    return f"{source_idx:02d}-{start_ms}-{end_ms}.mp4"
```

- [ ] **Step 4: Add `make_clip` to `bootleg/media/transcode.py`**

```python
# The locked clip profile. CHANGING ANY OF THESE BREAKS `-c copy` AGAINST
# EVERY CLIP EVER CUT: the concat demuxer refuses streams whose codec
# parameters differ, so a reel mixing an old clip and a new one either fails
# or produces artifacts. Sources that do not match are conformed at cut time
# rather than at concat time -- an upscale is a smaller price than a clip
# library that cannot be concatenated.
CLIP_WIDTH = 3840
CLIP_HEIGHT = 2160
CLIP_FPS = 30
CLIP_CRF = 20


def make_clip(
    src: Path,
    dst: Path,
    *,
    start_ms: int,
    end_ms: int,
    rotation_deg: int = 0,
) -> None:
    """Cut one span to the locked clip profile.

    Software libx264 on purpose, never a hardware encoder: those emit
    vendor-specific SPS/PPS headers, so a clip cut on the Mac and one cut on
    the 4070Ti would fail to concat cleanly or concat with artifacts. libx264
    produces identical headers on every machine, permanently. Roughly 30
    seconds per 20-second 4K clip, which is the right trade for an artifact
    that must stay byte-compatible for years.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    duration_ms = end_ms - start_ms
    if duration_ms <= 0:
        raise ValueError(f"clip needs a positive duration, got {duration_ms}ms")

    # Rotation FIRST, then scale, then pad. At 90 and 270 the rotation swaps
    # the frame's axes, so scaling before rotating pads against the wrong one.
    # Irrelevant at 0 and 180, wrong the moment the camera is mounted sideways.
    vf = ",".join(
        f
        for f in (
            rotation_filter(rotation_deg),
            f"scale={CLIP_WIDTH}:{CLIP_HEIGHT}:force_original_aspect_ratio=decrease",
            f"pad={CLIP_WIDTH}:{CLIP_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
        )
        if f
    )

    run_ffmpeg([
        # -noautorotate before the input, exactly as make_proxy does: a
        # rotation this library did not choose must never reach the filter
        # chain. -display_rotation 0 then strips stale Display Matrix side
        # data, so a rotation-aware player cannot double-rotate a clip whose
        # rotation is already baked into the pixels.
        "-noautorotate",
        "-display_rotation", "0",
        # -ss before -i is both fast and frame-accurate here, because the
        # output is always re-encoded. Accuracy is not optional: an in-point
        # landing on the previous keyframe would put a second of the wrong
        # footage at the head of the clip, and these boundaries were trimmed
        # by hand.
        "-ss", f"{start_ms / 1000:.3f}",
        "-i", str(src),
        "-t", f"{duration_ms / 1000:.3f}",
        "-vf", vf,
        # CFR at the locked rate. The first real source runs at 29.964 fps, so
        # this duplicates roughly one frame in 830 -- imperceptible, and
        # required, because mismatched frame rates break `-c copy`.
        "-r", str(CLIP_FPS),
        "-c:v", "libx264",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-crf", str(CLIP_CRF),
        "-preset", "medium",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(dst),
    ])
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_clips.py -q`
Expected: PASS, 10 passed. These encode real 4K frames; the file takes a minute or two.

- [ ] **Step 6: Run the full suite and the linter**

Run: `~/miniconda3/envs/bootleg/bin/pytest -q && ~/miniconda3/envs/bootleg/bin/ruff check bootleg tests`
Expected: all pass, ruff clean

- [ ] **Step 7: Commit**

```bash
git add bootleg/media/clips.py bootleg/media/transcode.py tests/test_clips.py
git commit -m "feat(media): cut a clip at the locked profile

3840x2160 30fps CFR CRF20 High yuv420p, AAC 128k 48k stereo, software
libx264. Locked because the concat demuxer refuses mismatched codec
parameters, so any change breaks -c copy against every clip ever cut.
Software encoding because hardware encoders emit vendor-specific SPS/PPS
headers and a clip cut on one machine would not concat with another's.

Rotation is baked, applied before scale and pad because a quarter turn swaps
the axes, and stale Display Matrix side data is stripped so a player cannot
double-rotate what is already in the pixels.

Filenames are span-derived, never idx-derived: _renumber reassigns idx on
every sweep. That also makes export incremental with no staleness record to
keep in sync."
```

---

### Task 5: The `clip` job

**Files:**
- Modify: `bootleg/jobs/handlers.py` (`handle_clip`, `HANDLERS`)
- Modify: `bootleg/db/rallies.py` (`set_clip_path`)
- Modify: `bootleg/db/jobs.py` (`has_pending_clip`)
- Test: `tests/test_handlers.py` (append)

**Interfaces:**
- Consumes: `make_clip`, `clip_relpath` (Task 4), `Library.clips_dir`/`require_free`, `find_original`, `get_source`.
- Produces: `handle_clip(library, payload) -> None` for payload `{"source_id", "rally_id", "start_ms", "end_ms"}`; `set_clip_path(conn, rally_id, clip_path)`; `has_pending_clip(conn, source_id, start_ms, end_ms) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_handler_clip.py` (a new file — `tests/test_handlers.py` is
already ~950 lines and this is a self-contained handler):

```python
import subprocess

import pytest

from bootleg.config import NotEnoughSpace
from bootleg.db.jobs import has_pending_clip
from bootleg.db.jobs import enqueue
from bootleg.db.rallies import replace_rallies
from bootleg.detect.segment import Interval
from bootleg.jobs.handlers import handle_clip
from bootleg.media.clips import clip_relpath
from bootleg.media.probe import probe
from bootleg.media.transcode import CLIP_HEIGHT, CLIP_WIDTH


@pytest.fixture
def a_rally(library, conn, registered_source):
    """One rally over a source whose original is on disk.

    registered_source's sample video is 2 s long, so the span stays inside it
    -- a cut past the end would produce a short clip and mask a real failure.
    """
    replace_rallies(conn, registered_source.session_id, registered_source.id,
                    [Interval(200, 1200, 0.8)])
    rally = conn.execute("SELECT * FROM rallies").fetchone()
    return {
        "payload": {
            "source_id": registered_source.id,
            "rally_id": rally["id"],
            "start_ms": 200,
            "end_ms": 1200,
        },
        "source": registered_source,
        "rally_id": rally["id"],
    }


def _clip_path(library, source, start_ms, end_ms):
    return library.clips_dir(source.session_id) / clip_relpath(source.idx, start_ms, end_ms)


def test_handle_clip_writes_a_span_named_file(library, conn, a_rally):
    handle_clip(library, a_rally["payload"])
    dst = _clip_path(library, a_rally["source"], 200, 1200)
    assert dst.exists()
    info = probe(dst)
    assert (info.width, info.height) == (CLIP_WIDTH, CLIP_HEIGHT)


def test_handle_clip_records_a_library_relative_clip_path(library, conn, a_rally):
    handle_clip(library, a_rally["payload"])
    stored = conn.execute(
        "SELECT clip_path FROM rallies WHERE id = ?", (a_rally["rally_id"],)
    ).fetchone()["clip_path"]
    # Library-relative, never absolute: the path is read on whatever machine
    # has the drive mounted, and that mountpoint differs between them.
    assert not stored.startswith("/")
    assert (library.root / stored).exists()


def test_handle_clip_is_idempotent(library, conn, a_rally):
    # reclaim_stale() can requeue a handler that already ran, so a second run
    # must overwrite rather than fail. A clip half-written by a killed worker
    # is worthless; re-encoding is the only safe retry.
    handle_clip(library, a_rally["payload"])
    handle_clip(library, a_rally["payload"])
    assert _clip_path(library, a_rally["source"], 200, 1200).exists()


def test_handle_clip_raises_on_an_unknown_source(library, conn, a_rally):
    payload = {**a_rally["payload"], "source_id": "nope"}
    with pytest.raises(ValueError, match="No such source"):
        handle_clip(library, payload)


def test_handle_clip_cuts_from_the_proxy_when_the_original_is_reclaimed(
    library, conn, a_rally
):
    source = a_rally["source"]
    # Stand in for a reclaimed source: proxy present, original flag cleared.
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(source.dir / "proxy.mp4")],
        check=True, capture_output=True,
    )
    conn.execute("UPDATE sources SET has_original = 0 WHERE id = ?", (source.id,))
    conn.commit()

    handle_clip(library, a_rally["payload"])
    info = probe(_clip_path(library, source, 200, 1200))
    # Conformed to the locked frame even from 1080p. A 1080p-sourced clip is
    # flagged in the UI, but it must stay concat-compatible -- that is the
    # property that cannot be compromised.
    assert (info.width, info.height) == (CLIP_WIDTH, CLIP_HEIGHT)


def test_handle_clip_refuses_before_encoding_when_space_is_short(
    library, conn, a_rally, monkeypatch
):
    def no_space(_need):
        raise NotEnoughSpace("nope")

    monkeypatch.setattr(library, "require_free", no_space)
    with pytest.raises(NotEnoughSpace):
        handle_clip(library, a_rally["payload"])
    # Refused before writing, not partway through: a 4K clip that dies at 90%
    # is worse than a job that never started.
    assert not _clip_path(library, a_rally["source"], 200, 1200).exists()


def test_has_pending_clip_distinguishes_two_spans_of_one_source(conn, a_rally):
    """The load-bearing case.

    has_pending_job matches on source_id alone, which is right for
    ingest/build_proxy/detect (one per source) and wrong for clips: one source
    yields dozens, so a source-wide check would let the first enqueued clip
    suppress every other clip from the same session.
    """
    source_id = a_rally["payload"]["source_id"]
    enqueue(conn, "clip", {"source_id": source_id, "rally_id": "r1",
                           "start_ms": 200, "end_ms": 1200})

    assert has_pending_clip(conn, source_id, 200, 1200) is True
    assert has_pending_clip(conn, source_id, 9000, 14000) is False
```

`test_has_pending_clip_distinguishes_two_spans_of_one_source` is the load-bearing one: without a span-aware check, the first of 24 clip jobs suppresses the other 23.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_handlers.py -q -k clip`
Expected: FAIL — `ImportError: cannot import name 'handle_clip'`

- [ ] **Step 3: Add `set_clip_path`**

In `bootleg/db/rallies.py`:

```python
def set_clip_path(conn: sqlite3.Connection, rally_id: str, clip_path: str) -> None:
    """Record the library-relative path of the clip cut for this rally.

    Stored rather than derived because a rally's bounds can move after its
    clip was cut -- the path here is what WAS cut, while clip_relpath() of the
    current bounds is what SHOULD be. Reclaim Space will need the former to
    know an original is safe to delete.
    """
    conn.execute("UPDATE rallies SET clip_path = ? WHERE id = ?", (clip_path, rally_id))
    conn.commit()
```

- [ ] **Step 4: Add `has_pending_clip`**

In `bootleg/db/jobs.py`:

```python
def has_pending_clip(
    conn: sqlite3.Connection, source_id: str, start_ms: int, end_ms: int
) -> bool:
    """True if a clip job for exactly this span is already queued or running.

    Separate from has_pending_job, which matches on source_id alone. That is
    right for ingest/build_proxy/detect, which are one-per-source, and wrong
    for clips: one source yields dozens, so a source-wide check would let the
    first enqueued clip suppress every other clip from the same session.
    """
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE type = 'clip' AND status IN ('queued', 'running')"
        " AND json_extract(payload, '$.source_id') = ?"
        " AND json_extract(payload, '$.start_ms') = ?"
        " AND json_extract(payload, '$.end_ms') = ? LIMIT 1",
        (source_id, start_ms, end_ms),
    ).fetchone()
    return row is not None
```

- [ ] **Step 5: Write `handle_clip`**

In `bootleg/jobs/handlers.py`:

```python
def handle_clip(library: Library, payload: dict) -> None:
    """Cut one rally's span to a clip at the locked profile.

    Idempotent by overwrite, like every other handler: a clip half-written by
    a killed worker is worthless, so a retry re-encodes rather than resuming.
    """
    conn = _open(library)
    source = get_source(conn, payload["source_id"])
    if source is None:
        raise ValueError(f"No such source: {payload['source_id']}")

    src_dir = library.source_dir(source["session_id"], source["idx"])
    if source["has_original"]:
        src = find_original(src_dir)
        if src is None:
            raise ValueError(f"No original on disk for source {source['id']}")
    else:
        # Reclaimed: cut from the proxy and let make_clip's scale/pad conform
        # it to the locked frame. The clip is 1080p-sourced, which the UI
        # flags, but it is still concat-compatible -- which is the property
        # that cannot be compromised.
        src = src_dir / "proxy.mp4"
        if not src.exists():
            raise ValueError(f"No proxy on disk for source {source['id']}")

    start_ms, end_ms = payload["start_ms"], payload["end_ms"]
    name = clip_relpath(source["idx"], start_ms, end_ms)
    dst = library.clips_dir(source["session_id"]) / name

    # A 4K CRF-20 clip runs roughly 4 MB per second of video. Doubling that
    # leaves room for the muxer's own scratch and refuses early rather than
    # dying at 90%, which is the whole point of the check.
    library.require_free(int((end_ms - start_ms) / 1000 * 4_000_000 * 2))

    make_clip(src, dst, start_ms=start_ms, end_ms=end_ms,
              rotation_deg=source["rotation_deg"])

    set_clip_path(conn, payload["rally_id"], str(dst.relative_to(library.root)))
```

Register it:

```python
HANDLERS: dict[str, Handler] = {
    "ingest": handle_ingest,
    "build_proxy": handle_build_proxy,
    "detect": handle_detect,
    "clip": handle_clip,
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_handlers.py -q`
Expected: PASS

- [ ] **Step 7: Run the full suite and the linter**

Run: `~/miniconda3/envs/bootleg/bin/pytest -q && ~/miniconda3/envs/bootleg/bin/ruff check bootleg tests`
Expected: all pass, ruff clean

- [ ] **Step 8: Commit**

```bash
git add bootleg/jobs/handlers.py bootleg/db/rallies.py bootleg/db/jobs.py \
        tests/test_handlers.py
git commit -m "feat(jobs): a clip job

Cuts one span at the locked profile, from the original when present and from
the proxy when the original has been reclaimed. Checks free space first,
because a 4K clip that dies at 90% is worse than a job that refuses to start.

has_pending_clip is span-aware rather than reusing has_pending_job, which
matches on source_id alone -- right for the one-per-source jobs, wrong for
clips, where a single source yields dozens and a source-wide check would let
the first enqueued clip suppress all the rest."
```

---

### Task 6: Export a set

**Files:**
- Modify: `bootleg/api/routes.py` (`POST /api/sessions/{session_id}/export`)
- Create: `bootleg/export.py` (`spans_to_cut`)
- Test: `tests/test_export.py`

**Interfaces:**
- Consumes: `clip_relpath`, `has_pending_clip`, `jobq.enqueue`, `list_rallies`, `get_source`.
- Produces:
  - `spans_to_cut(library, conn, session_id, which: str) -> list[dict]`
  - `POST /api/sessions/{session_id}/export` — body `{"which": "points"|"starred"}` → `{"queued": int, "already_cut": int, "total": int}`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_export.py`:

```python
import pytest
from fastapi.testclient import TestClient

from bootleg.api.app import create_app
from bootleg.db.jobs import enqueue
from bootleg.db.rallies import replace_rallies, set_point, set_rejected, set_star
from bootleg.db.sessions import add_source, find_or_create_session_for_date
from bootleg.detect.segment import Interval
from bootleg.export import spans_to_cut
from bootleg.media.clips import clip_relpath


@pytest.fixture
def client(library, conn):
    with TestClient(create_app(library)) as c:
        yield c


@pytest.fixture
def seeded(library, conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id, [
        Interval(1000, 5000, 0.8),
        Interval(9000, 14000, 0.7),
        Interval(20000, 26000, 0.6),
    ])
    rows = conn.execute("SELECT * FROM rallies ORDER BY idx").fetchall()
    return {"session_id": session_id, "source_id": source_id, "idx": idx, "rallies": rows}


def _touch_clip(library, session_id, idx, start_ms, end_ms):
    path = library.clips_dir(session_id) / clip_relpath(idx, start_ms, end_ms)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not really an mp4")
    return path


def test_points_set_returns_only_points(library, conn, seeded):
    set_point(conn, seeded["rallies"][0]["id"], True)
    set_star(conn, seeded["rallies"][1]["id"], True)
    spans = spans_to_cut(library, conn, seeded["session_id"], "points")
    assert [s["start_ms"] for s in spans] == [1000]


def test_starred_set_returns_only_starred(library, conn, seeded):
    set_point(conn, seeded["rallies"][0]["id"], True)
    set_star(conn, seeded["rallies"][1]["id"], True)
    spans = spans_to_cut(library, conn, seeded["session_id"], "starred")
    assert [s["start_ms"] for s in spans] == [9000]


def test_rejected_rallies_are_never_cut(library, conn, seeded):
    # A rejection says there is no rally here, so there is nothing to cut --
    # even if the flag survived from before the rejection.
    rally = seeded["rallies"][0]
    set_point(conn, rally["id"], True)
    set_rejected(conn, rally["id"], True)
    assert spans_to_cut(library, conn, seeded["session_id"], "points") == []


def test_a_span_whose_clip_exists_is_skipped(library, conn, seeded):
    """The incremental property: this is what stops a second export
    re-encoding twenty-four clips."""
    rally = seeded["rallies"][0]
    set_point(conn, rally["id"], True)
    _touch_clip(library, seeded["session_id"], seeded["idx"], 1000, 5000)
    assert spans_to_cut(library, conn, seeded["session_id"], "points") == []


def test_a_span_whose_bounds_moved_is_cut_again(library, conn, seeded):
    # No staleness record to keep in sync: the rally now resolves to a
    # different path, and that path does not exist.
    rally = seeded["rallies"][0]
    set_point(conn, rally["id"], True)
    _touch_clip(library, seeded["session_id"], seeded["idx"], 1000, 5000)
    conn.execute("UPDATE rallies SET start_ms = 1400 WHERE id = ?", (rally["id"],))
    conn.commit()
    spans = spans_to_cut(library, conn, seeded["session_id"], "points")
    assert [s["start_ms"] for s in spans] == [1400]


def test_a_span_with_a_clip_job_in_flight_is_not_queued_twice(library, conn, seeded):
    rally = seeded["rallies"][0]
    set_point(conn, rally["id"], True)
    enqueue(conn, "clip", {"source_id": seeded["source_id"], "rally_id": rally["id"],
                           "start_ms": 1000, "end_ms": 5000})
    assert spans_to_cut(library, conn, seeded["session_id"], "points") == []


def test_spans_to_cut_rejects_an_unknown_set(library, conn, seeded):
    with pytest.raises(ValueError, match="which must be one of"):
        spans_to_cut(library, conn, seeded["session_id"], "everything")


def test_route_reports_queued_against_already_cut(client, library, conn, seeded):
    for rally in seeded["rallies"][:2]:
        set_point(conn, rally["id"], True)
    _touch_clip(library, seeded["session_id"], seeded["idx"], 1000, 5000)

    r = client.post(f"/api/sessions/{seeded['session_id']}/export",
                    json={"which": "points"})
    assert r.status_code == 200
    assert r.json() == {"queued": 1, "already_cut": 1, "total": 2}
    queued = conn.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE type = 'clip'"
    ).fetchone()["n"]
    assert queued == 1


def test_route_404s_on_an_unknown_session(client, seeded):
    r = client.post("/api/sessions/nope/export", json={"which": "points"})
    assert r.status_code == 404


def test_route_422s_on_an_unknown_set(client, seeded):
    r = client.post(f"/api/sessions/{seeded['session_id']}/export",
                    json={"which": "everything"})
    assert r.status_code == 422
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_export.py -q`
Expected: collection error — no `bootleg.export`

- [ ] **Step 3: Write `bootleg/export.py`**

```python
import sqlite3

from bootleg.config import Library
from bootleg.db.jobs import has_pending_clip
from bootleg.db.sessions import get_source
from bootleg.media.clips import clip_relpath

SETS = ("points", "starred")


def spans_to_cut(
    library: Library, conn: sqlite3.Connection, session_id: str, which: str
) -> list[dict]:
    """The spans in `which` that have no clip yet and no clip job in flight.

    Incremental by construction rather than by bookkeeping: a clip either
    exists at the path its current bounds imply, or it does not. Nothing
    records staleness, because there is nothing to keep in sync -- a rally
    whose bounds moved simply resolves to a different path, which is missing.
    """
    if which not in SETS:
        raise ValueError(f"which must be one of {list(SETS)}, got {which!r}")

    # The f-string interpolates a column name chosen from a fixed tuple, never
    # from request data -- the guard above is what keeps that true, so it must
    # stay directly above this query rather than drifting into the caller.
    column = "point" if which == "points" else "starred"
    rows = conn.execute(
        f"SELECT * FROM rallies WHERE session_id = ? AND {column} = 1"
        " AND rejected = 0 ORDER BY idx",
        (session_id,),
    ).fetchall()

    clips_dir = library.clips_dir(session_id)
    sources: dict[str, sqlite3.Row] = {}
    pending: list[dict] = []

    for rally in rows:
        source = sources.get(rally["source_id"])
        if source is None:
            source = get_source(conn, rally["source_id"])
            if source is None:
                # A rally whose source vanished cannot be cut. Skip rather than
                # raise: one broken row must not block exporting the rest.
                continue
            sources[rally["source_id"]] = source

        name = clip_relpath(source["idx"], rally["start_ms"], rally["end_ms"])
        if (clips_dir / name).exists():
            continue
        if has_pending_clip(conn, rally["source_id"], rally["start_ms"], rally["end_ms"]):
            continue

        pending.append({
            "source_id": rally["source_id"],
            "rally_id": rally["id"],
            "start_ms": rally["start_ms"],
            "end_ms": rally["end_ms"],
        })

    return pending
```

The returned dicts are exactly the `clip` job payload, so the caller enqueues
them unchanged.

- [ ] **Step 4: Add the route**

`routes.py` does not import the job queue today — add it alongside the other
`bootleg.db` imports, letting ruff's `I001` place it:

```python
from bootleg.db import jobs as jobq
from bootleg.export import SETS, spans_to_cut
```

Then the body model and route:

```python
class ExportBody(BaseModel):
    which: str

    @field_validator("which")
    @classmethod
    def check_which(cls, v: str) -> str:
        if v not in SETS:
            raise ValueError(f"which must be one of {list(SETS)}")
        return v


@router.post("/api/sessions/{session_id}/export")
def api_export(session_id: str, body: ExportBody, request: Request):
    conn = _conn(request)
    if get_session(conn, session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    library = _library(request)

    pending = spans_to_cut(library, conn, session_id, body.which)
    for payload in pending:
        jobq.enqueue(conn, "clip", payload)

    column = "point" if body.which == "points" else "starred"
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM rallies WHERE session_id = ?"
        f" AND {column} = 1 AND rejected = 0",
        (session_id,),
    ).fetchone()["n"]
    return {"queued": len(pending), "already_cut": total - len(pending), "total": total}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_export.py -q`
Expected: PASS

- [ ] **Step 6: Run the full suite and the linter**

Run: `~/miniconda3/envs/bootleg/bin/pytest -q && ~/miniconda3/envs/bootleg/bin/ruff check bootleg tests`
Expected: all pass, ruff clean

- [ ] **Step 7: Commit**

```bash
git add bootleg/export.py bootleg/api/routes.py tests/test_export.py
git commit -m "feat(api): export a session's points or starred rallies as clips

Incremental by construction: a clip either exists at the path its current
bounds imply or it does not, so a second export after marking three more
points costs three encodes rather than twenty-four. Nothing records
staleness because there is nothing to keep in sync."
```

---

### Task 7: The export buttons, and a CLI

**Files:**
- Modify: `web/src/lib/api.ts` (`exportClips`)
- Modify: `web/src/components/QueueMode.svelte` (the "Session reviewed" panel)
- Modify: `bootleg/cli.py` (`bootleg clips export`)
- Test: `web/tests/queue.test.ts` or a new component test; `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `POST /api/sessions/{id}/export` (Task 6), `QueueController.pointCount`/`starredCount`.
- Produces: `api.exportClips(sessionId, which)`; `cmd_clips_export(args) -> int` behind `bootleg clips export <session_id> --set points|starred`.

- [ ] **Step 1: Add the API client method**

```ts
  exportClips: (sessionId: string, which: 'points' | 'starred') =>
    post(`/api/sessions/${sessionId}/export`, { which }),
```

- [ ] **Step 2: Replace the dead-end panel**

`QueueMode.svelte`'s `{:else if !current}` branch currently renders "Session reviewed" plus a comment reserving this exact spot for export. Replace that comment with two buttons showing live counts:

```
Export point clips (24)        Export starred clips (0)
```

Each calls `api.exportClips`, then reports the result through the existing toaster — `queued`, `already_cut` and `total`, so a second press visibly says "0 queued, 24 already cut" rather than looking broken. Disable a button whose count is zero. Encoding progress is the jobs badge's job; do not build a second progress UI.

Reel creation is **not** part of this plan — these buttons cut clips and nothing else. Plan B adds the reel actions beside them.

- [ ] **Step 3: Write and run a test for the panel**

Create `web/tests/export-buttons.test.ts`. Copy the `mockApi` /
`vi.mock('../src/lib/api')` / media-stub preamble from
`web/tests/queue-position-after-timeline.test.ts` verbatim, adding
`exportClips: vi.fn().mockResolvedValue({ queued: 2, already_cut: 0, total: 2 })`
to the mock, then:

```ts
describe('the reviewed panel exports clips', () => {
  let target: HTMLDivElement
  let instance: unknown

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.exportClips.mockResolvedValue({ queued: 2, already_cut: 0, total: 2 })
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
  })

  function reviewed() {
    // Every rally already judged, so QueueController.current is undefined and
    // the finished branch renders -- the panel that has been a dead end since
    // the review UI was built.
    return detailWith([
      rally('r1', 1, { point: 1, reviewed_at: '2026-08-19T11:00:00Z' }),
      rally('r2', 2, { point: 1, reviewed_at: '2026-08-19T11:01:00Z' }),
      rally('r3', 3, { starred: 1, reviewed_at: '2026-08-19T11:02:00Z' }),
    ])
  }

  function button(label: RegExp): HTMLButtonElement {
    const el = Array.from(target.querySelectorAll('button')).find((b) =>
      label.test(b.textContent ?? ''),
    )
    if (!el) throw new Error(`no button matching ${label}`)
    return el as HTMLButtonElement
  }

  it('shows both sets with their counts', async () => {
    mockApi.getSession.mockResolvedValue(reviewed())
    instance = mount(SessionHarness, { target })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Session reviewed/))

    expect(button(/point clips/i).textContent).toMatch(/2/)
    expect(button(/starred clips/i).textContent).toMatch(/1/)
  })

  it('exports the set the button names', async () => {
    mockApi.getSession.mockResolvedValue(reviewed())
    instance = mount(SessionHarness, { target })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Session reviewed/))

    button(/point clips/i).click()
    await vi.waitFor(() => expect(mockApi.exportClips).toHaveBeenCalled())
    expect(mockApi.exportClips).toHaveBeenCalledWith('s1', 'points')
  })

  it('reports already-cut so a second press does not look broken', async () => {
    mockApi.getSession.mockResolvedValue(reviewed())
    mockApi.exportClips.mockResolvedValue({ queued: 0, already_cut: 2, total: 2 })
    instance = mount(SessionHarness, { target })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Session reviewed/))

    button(/point clips/i).click()
    // Without this the second press is indistinguishable from a dead button.
    await vi.waitFor(() => expect(target.textContent).toMatch(/already cut/i))
  })

  it('disables a set with nothing in it', async () => {
    mockApi.getSession.mockResolvedValue(
      detailWith([rally('r1', 1, { point: 1, reviewed_at: '2026-08-19T11:00:00Z' })]),
    )
    instance = mount(SessionHarness, { target })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Session reviewed/))
    expect(button(/starred clips/i).disabled).toBe(true)
  })
})
```

`rally()` and `detailWith()` come from the same file you copied the preamble
from; extend its `rally()` helper to accept `point` in its overrides.

Run (from `web/`): `npx vitest run && npm run check`
Expected: all pass, svelte-check 0/0

- [ ] **Step 4: Add the CLI command**

In `bootleg/cli.py`, mirroring the `labels` subcommand's shape:

```python
def cmd_clips_export(args) -> int:
    library = _library(args)
    conn = connect(library.db_path)
    migrate(conn)
    if get_session(conn, args.session_id) is None:
        print(f"session not found: {args.session_id}", file=sys.stderr)
        return 1

    pending = spans_to_cut(library, conn, args.session_id, args.set)
    for payload in pending:
        jobq.enqueue(conn, "clip", payload)
    print(f"queued {len(pending)} clip job(s) for {args.set} in {args.session_id}")
    if not pending:
        print("nothing to cut -- every clip in that set already exists")
    return 0
```

Wire the parser with a `clips` group and an `export` subcommand taking `session_id` and `--set` (choices from `SETS`, default `points`).

- [ ] **Step 5: Write and run the CLI tests**

Append to `tests/test_cli.py` (`json`, `main`, `add_source` and
`find_or_create_session_for_date` are already imported at module level):

```python
def test_clips_export_queues_a_job_per_point(library, conn, capsys):
    from bootleg.db.rallies import replace_rallies, set_point
    from bootleg.detect.segment import Interval

    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7)])
    for row in conn.execute("SELECT id FROM rallies").fetchall():
        set_point(conn, row["id"], True)
    conn.close()

    rc = main(["--library", str(library.root), "clips", "export", session_id])
    assert rc == 0
    assert "queued 2 clip job(s)" in capsys.readouterr().out

    c = connect(library.db_path)
    assert c.execute("SELECT COUNT(*) FROM jobs WHERE type='clip'").fetchone()[0] == 2


def test_clips_export_a_second_time_queues_nothing_and_says_so(library, conn, capsys):
    from bootleg.db.rallies import replace_rallies, set_point
    from bootleg.detect.segment import Interval

    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id, [Interval(1000, 5000, 0.8)])
    set_point(conn, conn.execute("SELECT id FROM rallies").fetchone()["id"], True)
    conn.close()

    main(["--library", str(library.root), "clips", "export", session_id])
    capsys.readouterr()
    # The job from the first run is still queued, so the span is in flight and
    # must not be enqueued twice.
    rc = main(["--library", str(library.root), "clips", "export", session_id])
    assert rc == 0
    out = capsys.readouterr().out
    assert "queued 0 clip job(s)" in out
    assert "already exists" in out


def test_clips_export_on_an_unknown_session_fails(library, conn, capsys):
    conn.close()
    rc = main(["--library", str(library.root), "clips", "export", "nope"])
    assert rc == 1
    assert "not found" in capsys.readouterr().err.lower()
```

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_cli.py -q -k clips`
Expected: PASS, 3 passed

- [ ] **Step 6: Run everything**

Run: `~/miniconda3/envs/bootleg/bin/pytest -q && ~/miniconda3/envs/bootleg/bin/ruff check bootleg tests`
Then from `web/`: `npx vitest run && npm run check`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/api.ts web/src/components/QueueMode.svelte web/tests \
        bootleg/cli.py tests/test_cli.py
git commit -m "feat: export buttons on the reviewed panel, and bootleg clips export

The end-of-queue panel has been a dead end carrying a comment reserving this
spot since the review UI was built. Both buttons report queued vs already
cut, so a second press reads as '0 queued, 24 already cut' rather than
looking broken."
```

---

## Post-implementation

Once Task 7 is verified by hand against the real library:

- **CLAUDE.md** — document the locked clip profile and that it can never change, the span-derived clip naming, and that `replace_rallies` carries `point`. Correct the test count.
- **Verify on real footage**: run `bootleg clips export` for the 2026-08-18 session (24 points, ~9 minutes of encoding), then confirm with `ffprobe` that a cut clip matches the locked profile and carries no rotation side data despite the source being `rotation 180`. That last check is the one this whole plan turns on.

Plan B (reels, the builder, preview) is written after this lands.
