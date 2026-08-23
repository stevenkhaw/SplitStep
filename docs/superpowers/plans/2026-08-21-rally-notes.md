# Rally Notes and Burned-In Captions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One free-text note per rally, typed during queue review, burned into the bottom-left of the 4K clip at export.

**Architecture:** A `note` column on `rallies`, carried across a re-segment by the same >50% overlap rule stars use. Queue mode opens a one-line field on `N`; all of its logic lives in `web/src/lib/notes.ts`. At export the note is rendered to a transparent PNG by Pillow and composited by ffmpeg's `overlay` — this machine's ffmpeg has no `drawtext`. A caption's identity lives in the clip's filename, so a changed note implies a path that does not exist and the next export re-cuts it; no staleness column.

**Tech Stack:** Python 3.12 (sqlite3, FastAPI/Starlette, Pillow 12.3, ffmpeg CLI), Svelte 5 + TypeScript, pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-08-21-rally-notes-design.md`

## Global Constraints

- Python runs from the `splitstep` conda env by path: `~/miniconda3/envs/splitstep/bin/pytest`, `~/miniconda3/envs/splitstep/bin/ruff`. It is not the shell default.
- `pytest` runs with `filterwarnings = ["error"]`. A new warning fails the suite.
- ruff line-length is 100.
- Note cap is **120 characters**, enforced in both `splitstep/db/rallies.py::NOTE_MAX_CHARS` and `web/src/lib/notes.ts::NOTE_MAX_CHARS`.
- Migrations are numbered `.sql` files applied by `PRAGMA user_version`. Add a file; never edit an applied one.
- Comments explain **why**, not what. This codebase carries long rationale comments on non-obvious calls. Match that density.
- All frontend logic goes in `web/src/lib/`, never in a `.svelte` file — jsdom has no `<video>`, so components are verified by hand and only `lib/` is testable.
- YOLO is never run in tests. No test may invoke a real detector.
- **Phase 2 (Tasks 5–7) is blocked** until the reels-builder branch merges — that work owns `splitstep/export.py`, `splitstep/jobs/handlers.py`, and `splitstep/media/`. Do not start Task 5 before confirming it has landed. Tasks 1–4 touch none of those files.

**One correction to the spec:** §3 says the route rejects an over-long note with a 400. Pydantic validators in this codebase (e.g. `PresetCreateBody.check_points`) surface as **422**, and Task 2 follows that existing behavior rather than adding custom exception handling for one route.

---

## Phase 1 — Capture and storage (unblocked)

### Task 1: The `note` column and its carry-across

**Files:**
- Create: `splitstep/db/migrations/006_rally_notes.sql`
- Modify: `splitstep/db/rallies.py` (add `NOTE_MAX_CHARS`, `_carried_note`, extend `replace_rallies`)
- Test: `tests/test_rally_notes.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `rallies.note TEXT NOT NULL DEFAULT ''`; `splitstep.db.rallies.NOTE_MAX_CHARS: int = 120`; `_carried_note(iv: Interval, rows: list[sqlite3.Row]) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rally_notes.py`:

```python
import pytest

from splitstep.db.rallies import list_rallies, replace_rallies
from splitstep.db.sessions import add_source, find_or_create_session_for_date
from splitstep.detect.segment import Interval


@pytest.fixture
def seeded(conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, _idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    return {"session_id": session_id, "source_id": source_id}


def _rows(conn, seeded):
    return list_rallies(conn, seeded["session_id"])


def test_note_defaults_to_empty_string(conn, seeded):
    # NOT NULL DEFAULT '' rather than a nullable column: every reader then
    # handles one absent-note representation instead of two.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    assert _rows(conn, seeded)[0]["note"] == ""


def test_note_carries_across_a_resegment_by_overlap(conn, seeded):
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rows(conn, seeded)[0]["id"]
    conn.execute("UPDATE rallies SET note = ? WHERE id = ?", ("late on the backhand", rally_id))
    conn.commit()

    # Same rally, boundaries nudged -- the case a threshold sweep produces.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1200, 4800, 0.8)])
    assert _rows(conn, seeded)[0]["note"] == "late on the backhand"


def test_note_is_dropped_when_nothing_overlaps_by_more_than_half(conn, seeded):
    # A rally the new threshold stops detecting takes its note with it, exactly
    # as it takes its star. Stated in the spec as a chosen consequence.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rows(conn, seeded)[0]["id"]
    conn.execute("UPDATE rallies SET note = ? WHERE id = ?", ("gone", rally_id))
    conn.commit()

    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(20_000, 24_000, 0.8)])
    assert _rows(conn, seeded)[0]["note"] == ""


def test_the_longest_overlap_wins_when_two_old_rallies_qualify(conn, seeded):
    # Two short notes, one long new span covering both. Ambiguity is resolved
    # by overlap rather than by row order, so the result does not depend on
    # what sqlite happens to return first.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 3000, 0.8), Interval(3000, 9000, 0.8)])
    rows = _rows(conn, seeded)
    conn.execute("UPDATE rallies SET note = ? WHERE id = ?", ("short one", rows[0]["id"]))
    conn.execute("UPDATE rallies SET note = ? WHERE id = ?", ("long one", rows[1]["id"]))
    conn.commit()

    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 9000, 0.8)])
    assert _rows(conn, seeded)[0]["note"] == "long one"


def test_a_note_alone_keeps_a_rally_in_the_carry_over_read_back(conn, seeded):
    # The read-back's WHERE clause selects rows worth carrying. A rally with a
    # note but no star, no point, no rejection and no clip is one of them --
    # without the note != '' term it would not be read back at all.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rows(conn, seeded)[0]["id"]
    conn.execute("UPDATE rallies SET note = ? WHERE id = ?", ("only a note", rally_id))
    conn.commit()

    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1100, 5100, 0.8)])
    assert _rows(conn, seeded)[0]["note"] == "only a note"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_rally_notes.py -v`
Expected: all five FAIL, `sqlite3.OperationalError: no such column: note`.

- [ ] **Step 3: Write the migration**

Create `splitstep/db/migrations/006_rally_notes.sql`:

```sql
-- One free-text note per rally, written during review and burned into the
-- clip at export.
--
-- A column on `rallies` rather than a row in `rally_labels`, which is the
-- opposite of what 003 chose for labels, and deliberately so. A label is a
-- judgement about the *detector's* span and has to outlive any number of
-- re-segments to stay useful for scoring. A note is about the clip you cut:
-- it belongs to the rally's current bounds, and anchoring it to a detector
-- span the reviewer has since dragged elsewhere would re-attach text to
-- boundaries it was never written about.
--
-- NOT NULL DEFAULT '' rather than nullable: absence has one representation,
-- so no reader has to handle NULL and '' as separate cases.
ALTER TABLE rallies ADD COLUMN note TEXT NOT NULL DEFAULT '';
```

- [ ] **Step 4: Carry the note across a re-segment**

In `splitstep/db/rallies.py`, add the cap constant beside `STAR_OVERLAP_MIN`:

```python
# The longest note that still renders as two lines inside the caption pill at
# 4K without shrinking the type. Enforced here and again at the API boundary
# (NoteBody), and mirrored in web/src/lib/notes.ts -- a note that cannot be
# rendered must never reach the database, whoever is writing it.
NOTE_MAX_CHARS = 120
```

Add `_carried_note` directly below `_carried_clip_path`:

```python
def _carried_note(iv: Interval, rows: list[sqlite3.Row]) -> str:
    """The note for `iv` from the best-overlapping old row, else ''.

    The >50% rule starred/rejected/point use, not clip_path's exact-span
    rule: a note is a judgement about a rally, and a rally that shifts by a
    few hundred milliseconds under a new threshold is the same rally the
    reviewer wrote about. clip_path is different because it names a file cut
    for one specific span.

    Best overlap rather than first match, unlike _overlaps_any. Those three
    are booleans, so any qualifying row gives the same answer; a note is a
    string, and two old rallies can both clear 50% of one merged new span. The
    answer must not depend on the order sqlite returned the rows in.
    """
    best_note, best_overlap = "", 0.0
    for r in rows:
        if not r["note"]:
            continue
        f = overlap_fraction(iv.start_ms, iv.end_ms, r["start_ms"], r["end_ms"])
        if f >= STAR_OVERLAP_MIN and f > best_overlap:
            best_note, best_overlap = r["note"], f
    return best_note
```

In `replace_rallies`, extend the read-back to select and qualify on `note` (the `note != ''` term is what keeps a note-only rally in the candidate set at all):

```python
        old = conn.execute(
            "SELECT start_ms, end_ms, starred, rejected, point, clip_path, note FROM rallies"
            " WHERE source_id = ? AND (starred = 1 OR rejected = 1 OR point = 1"
            " OR clip_path IS NOT NULL OR note != '')",
            (source_id,),
        ).fetchall()
```

Then, beside the existing `clip_path = _carried_clip_path(iv, old)` line:

```python
            note = _carried_note(iv, old)
```

And extend the INSERT:

```python
            conn.execute(
                "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
                "det_start_ms,det_end_ms,confidence,starred,rejected,point,clip_path,note)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, session_id, source_id, -placeholder_idx, iv.start_ms,
                 iv.end_ms, iv.start_ms, iv.end_ms, iv.confidence,
                 int(starred), int(rejected), int(point), clip_path, note),
            )
```

Update the `replace_rallies` docstring's first line to name the note:

```python
    """Rewrite one source's rallies, carrying stars, rejections, points and
    notes across by overlap, and clip_path across by exact span match (see
    _carried_clip_path).
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_rally_notes.py -v`
Expected: 5 passed.

- [ ] **Step 6: Run the full suite and the linter**

Run: `~/miniconda3/envs/splitstep/bin/pytest -q && ~/miniconda3/envs/splitstep/bin/ruff check splitstep tests`
Expected: all tests pass (471 + 5 new), ruff clean. `tests/test_db.py` and `tests/test_rallies_point.py` exercise `replace_rallies` heavily — if either fails, the INSERT column list and its value tuple have drifted out of step.

- [ ] **Step 7: Commit**

```bash
git add splitstep/db/migrations/006_rally_notes.sql splitstep/db/rallies.py tests/test_rally_notes.py
git commit -m "feat(db): a note per rally, carried across a re-segment by overlap"
```

---

### Task 2: `set_note` and the write route

**Files:**
- Modify: `splitstep/db/rallies.py` (add `set_note`)
- Modify: `splitstep/api/routes.py` (add `NoteBody`, `api_note`, import `set_note`)
- Test: `tests/test_rally_notes.py` (extend), `tests/test_api.py` (extend)

**Interfaces:**
- Consumes: `NOTE_MAX_CHARS` from Task 1.
- Produces: `set_note(conn: sqlite3.Connection, rally_id: str, note: str) -> None`; `POST /api/rallies/{rally_id}/note` with body `{"note": str}` returning `{"ok": True}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_rally_notes.py`:

```python
def test_set_note_writes_and_overwrites(conn, seeded):
    from splitstep.db.rallies import set_note

    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rows(conn, seeded)[0]["id"]

    set_note(conn, rally_id, "first")
    assert _rows(conn, seeded)[0]["note"] == "first"

    set_note(conn, rally_id, "second")
    assert _rows(conn, seeded)[0]["note"] == "second"


def test_set_note_does_not_stamp_reviewed_at(conn, seeded):
    # Unlike set_star/set_point/set_rejected. Those are verdicts on the rally;
    # a note is not one. "check this later" is a perfectly ordinary note, and
    # flipping the session to reviewed because someone typed it would report a
    # judgement nobody made.
    from splitstep.db.rallies import set_note

    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = _rows(conn, seeded)[0]["id"]

    set_note(conn, rally_id, "check this later")
    assert _rows(conn, seeded)[0]["reviewed_at"] is None
```

Append to `tests/test_api.py` (match the existing client fixture in that file — read the top of it first and reuse whatever fixture name the other rally-route tests use):

```python
def test_note_route_writes_the_note(client, seeded_session):
    rally_id = client.get(f"/api/sessions/{seeded_session}").json()["rallies"][0]["id"]

    r = client.post(f"/api/rallies/{rally_id}/note", json={"note": "  late on the backhand  "})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    # Trimmed at the boundary, so what the exporter renders is what the
    # reviewer meant -- trailing spaces would silently widen the caption pill.
    rallies = client.get(f"/api/sessions/{seeded_session}").json()["rallies"]
    assert rallies[0]["note"] == "late on the backhand"


def test_note_route_refuses_an_over_long_note(client, seeded_session):
    rally_id = client.get(f"/api/sessions/{seeded_session}").json()["rallies"][0]["id"]

    r = client.post(f"/api/rallies/{rally_id}/note", json={"note": "x" * 121})
    # 422, the same shape every other pydantic validator in this router
    # produces -- the UI is not the only writer a library ever has.
    assert r.status_code == 422

    rallies = client.get(f"/api/sessions/{seeded_session}").json()["rallies"]
    assert rallies[0]["note"] == ""


def test_note_route_accepts_a_note_at_exactly_the_cap(client, seeded_session):
    rally_id = client.get(f"/api/sessions/{seeded_session}").json()["rallies"][0]["id"]

    r = client.post(f"/api/rallies/{rally_id}/note", json={"note": "x" * 120})
    assert r.status_code == 200


def test_note_route_clears_a_note_with_an_empty_string(client, seeded_session):
    # Deleting a note is the same write as setting one. A separate DELETE
    # route would be a second code path for "the note is now empty".
    rally_id = client.get(f"/api/sessions/{seeded_session}").json()["rallies"][0]["id"]

    client.post(f"/api/rallies/{rally_id}/note", json={"note": "typo"})
    client.post(f"/api/rallies/{rally_id}/note", json={"note": ""})

    rallies = client.get(f"/api/sessions/{seeded_session}").json()["rallies"]
    assert rallies[0]["note"] == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_rally_notes.py tests/test_api.py -k note -v`
Expected: FAIL — `ImportError: cannot import name 'set_note'` for the db tests, 404 for the route tests.

- [ ] **Step 3: Write `set_note`**

In `splitstep/db/rallies.py`, beside `set_point`:

```python
def set_note(conn: sqlite3.Connection, rally_id: str, note: str) -> None:
    """Write (or clear) a rally's note.

    Deliberately does NOT stamp reviewed_at, unlike set_star/set_point/
    set_rejected. Those three are rulings on the clip and reviewed_at records
    that a human ruled on it; a note carries no verdict at all -- "check this
    later" is an ordinary thing to write -- so flipping a session to reviewed
    on the strength of one would report a judgement nobody made.
    """
    conn.execute("UPDATE rallies SET note = ? WHERE id = ?", (note, rally_id))
    conn.commit()
```

- [ ] **Step 4: Add the route**

In `splitstep/api/routes.py`, add to the imports from `splitstep.db.rallies` (the block that already imports `set_bounds`, `set_point`): `NOTE_MAX_CHARS`, `set_note`.

Add the body model beside `PointBody`:

```python
class NoteBody(BaseModel):
    note: str

    @field_validator("note")
    @classmethod
    def check_note(cls, v: str) -> str:
        # Trim before measuring, and store what was measured: trailing
        # whitespace is invisible to the reviewer but would widen the rendered
        # caption pill, and a note that is 120 characters of text plus two
        # spaces is not over the limit in any sense the reviewer would accept.
        v = v.strip()
        if len(v) > NOTE_MAX_CHARS:
            raise ValueError(f"a note is at most {NOTE_MAX_CHARS} characters")
        return v
```

Add the route beside `api_point`:

```python
@router.post("/api/rallies/{rally_id}/note")
def api_note(rally_id: str, body: NoteBody, request: Request):
    conn = _conn(request)
    # No refresh_session_review_status call, unlike star/reject/point: writing
    # a note is not a ruling on the rally (see set_note), so it must not move
    # the session's review status. Same reasoning the label route follows.
    set_note(conn, rally_id, body.note)
    return {"ok": True}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_rally_notes.py tests/test_api.py -k note -v`
Expected: all pass.

- [ ] **Step 6: Run the full suite and the linter**

Run: `~/miniconda3/envs/splitstep/bin/pytest -q && ~/miniconda3/envs/splitstep/bin/ruff check splitstep tests`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add splitstep/db/rallies.py splitstep/api/routes.py tests/test_rally_notes.py tests/test_api.py
git commit -m "feat(api): POST /api/rallies/{id}/note"
```

---

### Task 3: `web/src/lib/notes.ts`

**Files:**
- Create: `web/src/lib/notes.ts`
- Create: `web/tests/notes.test.ts`
- Modify: `web/src/lib/types.ts` (add `note` to `Rally`)
- Modify: `web/src/lib/api.ts` (add `setNote`)

**Interfaces:**
- Consumes: `POST /api/rallies/{id}/note` from Task 2.
- Produces: `NOTE_MAX_CHARS: 120`; `normalizeNote(raw: string): string`; `isDirty(buffer: string, saved: string): boolean`; `seedNotes(rallies: Rally[]): Map<string, string>`; `api.setNote(id: string, note: string): Promise<unknown>`; `Rally.note: string`.

- [ ] **Step 1: Write the failing tests**

Create `web/tests/notes.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { NOTE_MAX_CHARS, isDirty, normalizeNote, seedNotes } from '../src/lib/notes'
import type { Rally } from '../src/lib/types'

function rally(overrides: Partial<Rally> = {}): Rally {
  return {
    id: 'r1',
    session_id: 's1',
    source_id: 'src1',
    idx: 1,
    start_ms: 1000,
    end_ms: 2000,
    det_start_ms: 1000,
    det_end_ms: 2000,
    confidence: 0.9,
    starred: 0,
    rejected: 0,
    point: 0,
    reviewed_at: null,
    note: '',
    ...overrides,
  }
}

describe('normalizeNote', () => {
  it('trims, because trailing space silently widens the rendered caption', () => {
    expect(normalizeNote('  late on the backhand  ')).toBe('late on the backhand')
  })

  it('truncates at the cap rather than rejecting', () => {
    // The field stops accepting input at the cap, so a longer value only
    // arrives by paste. Silently keeping the first 120 characters beats
    // throwing away what the reviewer just pasted.
    expect(normalizeNote('x'.repeat(200))).toHaveLength(NOTE_MAX_CHARS)
  })

  it('trims before measuring, so trailing spaces cannot push a note over', () => {
    expect(normalizeNote('x'.repeat(120) + '   ')).toHaveLength(NOTE_MAX_CHARS)
  })

  it('leaves a note at exactly the cap alone', () => {
    expect(normalizeNote('x'.repeat(120))).toHaveLength(NOTE_MAX_CHARS)
  })
})

describe('isDirty', () => {
  it('is false when only whitespace differs', () => {
    // Opening the field and closing it must not cost a POST.
    expect(isDirty('  same  ', 'same')).toBe(false)
  })

  it('is true for a real edit', () => {
    expect(isDirty('changed', 'same')).toBe(true)
  })

  it('is true when clearing an existing note', () => {
    expect(isDirty('', 'had one')).toBe(true)
  })

  it('is false for two empties', () => {
    expect(isDirty('   ', '')).toBe(false)
  })
})

describe('seedNotes', () => {
  it('maps rally id to note', () => {
    const m = seedNotes([rally({ id: 'a', note: 'one' }), rally({ id: 'b', note: 'two' })])
    expect(m.get('a')).toBe('one')
    expect(m.get('b')).toBe('two')
  })

  it('omits rallies with no note, so `has` answers the indicator question', () => {
    const m = seedNotes([rally({ id: 'a', note: '' }), rally({ id: 'b', note: 'two' })])
    expect(m.has('a')).toBe(false)
    expect(m.has('b')).toBe(true)
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npx vitest run tests/notes.test.ts`
Expected: FAIL — cannot resolve `../src/lib/notes`.

- [ ] **Step 3: Write the module**

Create `web/src/lib/notes.ts`:

```ts
import type { Rally } from './types'

/**
 * The longest note that still renders as two lines inside the caption pill at
 * 4K without shrinking the type. Mirrors NOTE_MAX_CHARS in
 * splitstep/db/rallies.py -- the server enforces it too, because the UI is not
 * the only writer a library ever has.
 */
export const NOTE_MAX_CHARS = 120

/**
 * What actually gets sent and stored: trimmed, then capped.
 *
 * Trim happens BEFORE the cap so 120 characters of text followed by spaces is
 * not treated as over the limit -- the same order the server's validator uses,
 * so the two can never disagree about whether a note fits.
 */
export function normalizeNote(raw: string): string {
  return raw.trim().slice(0, NOTE_MAX_CHARS)
}

/**
 * Whether committing `buffer` would change what the server holds.
 *
 * Compares normalized forms, so opening the field and closing it -- or adding
 * and removing a trailing space -- costs no POST at all.
 */
export function isDirty(buffer: string, saved: string): boolean {
  return normalizeNote(buffer) !== normalizeNote(saved)
}

/**
 * Rally id -> note, for the notes a session actually has.
 *
 * Empty notes are omitted rather than stored as '', so `has(id)` is the whole
 * question the ✎ indicator asks and no caller has to distinguish "absent" from
 * "present but empty".
 */
export function seedNotes(rallies: Rally[]): Map<string, string> {
  const m = new Map<string, string>()
  for (const r of rallies) {
    if (r.note) m.set(r.id, r.note)
  }
  return m
}
```

- [ ] **Step 4: Extend the Rally type and the api client**

In `web/src/lib/types.ts`, add to `interface Rally` after `reviewed_at`:

```ts
  note: string
```

In `web/src/lib/api.ts`, beside `point`:

```ts
  setNote: (id: string, note: string) => post(`/api/rallies/${id}/note`, { note }),
```

- [ ] **Step 5: Run the tests and the type check**

Run: `cd web && npx vitest run tests/notes.test.ts && npm run check`
Expected: notes tests pass. `npm run check` will report errors in **test files** that build a `Rally` literal without `note` — fix each by adding `note: ''` to the fixture. Do not add `note` as optional to silence them; the server always sends it.

- [ ] **Step 6: Run the whole frontend suite**

Run: `cd web && npx vitest run && npm run check`
Expected: all green, 0 errors 0 warnings.

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/notes.ts web/src/lib/types.ts web/src/lib/api.ts web/tests
git commit -m "feat(web): notes module, Rally.note, setNote client"
```

---

### Task 4: The note field in queue mode

**Files:**
- Modify: `web/src/components/QueueMode.svelte`
- Test: `web/tests/editable-target-guard.test.ts` (extend)

**Interfaces:**
- Consumes: `normalizeNote`, `isDirty`, `seedNotes`, `NOTE_MAX_CHARS` (Task 3); `api.setNote` (Task 3).
- Produces: nothing later tasks depend on.

Note for the implementer: notes stay **out** of `QueueController` and out of `persist.ts`. Those model three booleans with an undo stack; a note is free text with no toggle semantics, and threading it through `QueueAction` would put text into an undo history that exists to walk back verdicts.

- [ ] **Step 1: Write the failing test**

In `web/tests/editable-target-guard.test.ts`, add a case beside the preset-name one. The file already has `expandPanels()`, `keydownOn()` and `presetNameInput()` helpers — reuse them and add:

```ts
  function noteInput(): HTMLInputElement {
    const el = target.querySelector('input[aria-label="rally note"]')
    if (!el) throw new Error('note input not found')
    return el as HTMLInputElement
  }

  it('lets a note with spaces be typed after N without starring/skipping/undoing', async () => {
    instance = mount(Session, { target, props: { id: 's1' } })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 1 \/ 2/))

    // N opens the field. The window handler is what must NOT act on the
    // keystrokes that follow.
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'n', bubbles: true }))
    flushSync()

    const input = noteInput()
    input.focus()

    for (const key of ['s', 'l', 'o', 'w', ' ', 'x']) {
      const notPrevented = keydownOn(input, key)
      if (key === ' ') expect(notPrevented).toBe(true)
      input.value += key
      input.dispatchEvent(new Event('input', { bubbles: true }))
      flushSync()
    }

    expect(input.value).toBe('slow x')
    // 's' would star, 'x' would reject, and space would toggle playback.
    expect(mockApi.star).not.toHaveBeenCalled()
    expect(mockApi.reject).not.toHaveBeenCalled()
  })
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && npx vitest run tests/editable-target-guard.test.ts`
Expected: FAIL with "note input not found".

- [ ] **Step 3: Add the state and handlers to QueueMode**

In the `<script>` block of `web/src/components/QueueMode.svelte`, add the import beside the others:

```ts
  import { NOTE_MAX_CHARS, isDirty, normalizeNote, seedNotes } from '../lib/notes'
```

Add state beside `speed` / `scrubTrack`:

```ts
  // Rally id -> note, seeded once from the server's rows and kept current as
  // the reviewer types. Not derived from `detail` on every render: a commit
  // updates this map immediately, and re-deriving would show the stale
  // server value until the next session refetch.
  let notes = $state(seedNotes(detail.rallies))
  let editingNote = $state(false)
  let noteBuffer = $state('')
  let noteInput = $state<HTMLInputElement>()
```

Add the handlers beside `onProgress`:

```ts
  function openNote() {
    if (!current) return
    noteBuffer = notes.get(current.id) ?? ''
    editingNote = true
    // Focus after the field exists. Svelte renders on the microtask queue, so
    // the element is not in the DOM at the point this handler returns.
    queueMicrotask(() => noteInput?.select())
  }

  async function commitNote() {
    const rally = current
    if (!rally) return
    const next = normalizeNote(noteBuffer)
    const saved = notes.get(rally.id) ?? ''
    editingNote = false
    // Opening the field and closing it unchanged must not cost a request --
    // this is pressed on rallies the reviewer only wanted to read.
    if (!isDirty(next, saved)) return

    // Optimistic, matching how star/point/reject already behave: this runs on
    // a LAN box, and making the reviewer wait on a round trip per note would
    // break the rhythm the whole queue exists to protect.
    if (next) notes.set(rally.id, next)
    else notes.delete(rally.id)
    notes = new Map(notes)

    try {
      await api.setNote(rally.id, next)
    } catch (e) {
      // Put back exactly what the server last accepted. Unlike a failed star
      // there is no revert action to hand the controller -- notes are not
      // part of the undo stack -- so the restore happens here.
      if (saved) notes.set(rally.id, saved)
      else notes.delete(rally.id)
      notes = new Map(notes)
      toaster.push(`Couldn't save the note -- ${String(e)}`)
    }
  }

  function onNoteKey(e: KeyboardEvent) {
    // Handled on the field itself rather than in onKey: these two keys mean
    // commit and cancel only while the field is open, and giving them a
    // second global meaning would make them depend on what has focus.
    if (e.key === 'Enter') {
      e.preventDefault()
      commitNote()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      // Cancel, not commit: Escape discards, which is why noteBuffer is
      // never written back to `notes` here.
      editingNote = false
    }
  }
```

Add the key case in `onKey`, beside `case 't'`:

```ts
      case 'n':
      case 'N':
        e.preventDefault()
        openNote()
        break
```

- [ ] **Step 4: Add the markup**

In `web/src/components/QueueMode.svelte`, directly after the scrub-bar `<details>`-free `</div>` that closes the track and before the metadata line, add:

```svelte
  {#if editingNote}
    <!-- One line, and no textarea: the caption renders as at most two lines at
         4K, so a field that invites a paragraph would invite text the export
         has to truncate. Enter/Escape are handled on the field itself (see
         onNoteKey); the queue's window handler already stands down for an
         editable target, which editable-target-guard.test.ts pins. -->
    <input
      bind:this={noteInput}
      bind:value={noteBuffer}
      onkeydown={onNoteKey}
      onblur={commitNote}
      maxlength={NOTE_MAX_CHARS}
      placeholder="note for this rally — Enter saves, Esc cancels"
      aria-label="rally note"
      class="mt-2 w-full rounded border border-neutral-700 bg-neutral-900 px-2 py-1
             font-mono text-sm"
    />
  {/if}
```

In the metadata line, add the indicator beside the existing star and point spans:

```svelte
      <span
        class="text-base leading-none {current && notes.has(current.id)
          ? 'text-blue-300'
          : 'text-neutral-700'}"
        title={current && notes.has(current.id) ? (notes.get(current.id) ?? '') : 'no note'}
      >✎</span>
```

Update the hint line to include the key:

```
    S star · P point · X reject (again to undo) · R replay · N note · ← back · → next · U undo · `/1/2/3 speed · T timeline · L label
```

- [ ] **Step 5: Run the tests and the type check**

Run: `cd web && npx vitest run && npm run check`
Expected: all tests pass, 0 errors 0 warnings.

- [ ] **Step 6: Verify by hand in the real app**

Run: `cd web && npm run build`, then with `splitstep serve` running, open a session, press `N`, type a note with a space in it, press Enter. Confirm: the ✎ turns blue, star/reject did not fire on the letters typed, reloading the page shows the note still there, and Escape on a second edit discards it.

- [ ] **Step 7: Commit**

```bash
git add web/src/components/QueueMode.svelte web/tests/editable-target-guard.test.ts
git commit -m "feat(queue): N writes a note on the current rally"
```

---

## Phase 2 — Burn-in (blocked on the reels branch)

**Before starting Task 5:** confirm the reels-builder work has merged (`git log --oneline -20`, look for reel/concat commits) and rebase onto it. Tasks 5–7 touch `splitstep/media/` and `splitstep/export.py`, which that work owns until then.

### Task 5: `render_caption`

**Files:**
- Create: `splitstep/media/caption.py`
- Test: `tests/test_caption.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `render_caption(text: str, width: int, height: int) -> Image.Image | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_caption.py`:

```python
from splitstep.media.caption import render_caption

W, H = 3840, 2160


def test_no_note_renders_nothing():
    # None rather than a fully transparent image: "no caption" is then a
    # single check at the top of the pipeline instead of an overlay input
    # that costs an encode pass to composite nothing.
    assert render_caption("", W, H) is None
    assert render_caption("   ", W, H) is None


def test_the_overlay_is_the_size_of_the_frame():
    # Composited at 0,0 over the final 4K frame, so it must be that frame's
    # size -- position lives in the rendered pixels, not in overlay's x/y.
    img = render_caption("late on the backhand", W, H)
    assert img is not None
    assert img.size == (W, H)
    assert img.mode == "RGBA"


def test_everything_outside_the_pill_is_transparent():
    img = render_caption("late on the backhand", W, H)
    assert img is not None
    # Top-left corner: nowhere near a bottom-left caption.
    assert img.getpixel((10, 10))[3] == 0
    # Dead centre, where play happens.
    assert img.getpixel((W // 2, H // 2))[3] == 0


def test_the_pill_is_opaque_enough_to_read_against_bright_footage():
    img = render_caption("late on the backhand", W, H)
    assert img is not None
    # A point inside the pill: bottom-left, up from the bottom edge by the
    # safe margin. Floodlit night footage is the worst case for legibility.
    alpha = img.getpixel((150, H - 150))[3]
    assert alpha > 100


def test_a_long_note_wraps_to_a_taller_pill_rather_than_overflowing():
    short = render_caption("short", W, H)
    long = render_caption("x" * 120, W, H)
    assert short is not None and long is not None

    def pill_top(img):
        for y in range(img.height - 1, -1, -1):
            if img.getpixel((150, y))[3] > 0:
                continue
            return y
        return 0

    # The wrapped caption's pill starts higher up the frame than the one-liner's.
    assert pill_top(long) < pill_top(short)


def test_the_caption_never_reaches_the_frame_edges():
    # A caption touching the edge reads as a rendering bug, and any later
    # crop or platform-side safe area would clip it.
    img = render_caption("x" * 120, W, H)
    assert img is not None
    for y in range(H):
        assert img.getpixel((0, y))[3] == 0
    for x in range(W):
        assert img.getpixel((x, H - 1))[3] == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_caption.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'splitstep.media.caption'`.

- [ ] **Step 3: Write the module**

Create `splitstep/media/caption.py`:

```python
"""Render a rally's note to a transparent overlay for burn-in at clip export.

Pillow rather than ffmpeg's `drawtext`, and not as a preference: the ffmpeg
this library runs against (Homebrew 9.0.1) is built without libfreetype and
without libass, so neither `drawtext` nor `subtitles` exists in it at all.
Requiring a rebuild would make a clip's appearance depend on how each
machine's ffmpeg was compiled -- the same portability trap "software libx264,
never a hardware encoder" exists to avoid, and a worse one, because a caption
that fails to render leaves no artifact to notice.

Everything here is pure Pillow, so wrapping, the two-line cap and the empty
case are all testable without ffmpeg or a video file.
"""
from PIL import Image, ImageDraw, ImageFont

# Tried in order. Nothing in this repo ships a font, so the caption's face
# comes from the host; the list ends at Pillow's own bitmap font so a machine
# with none of these renders something ugly rather than raising mid-export.
FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)

# All geometry is a fraction of frame height, so the same numbers hold if the
# locked clip profile ever changes resolution.
MARGIN_FRAC = 0.045
FONT_FRAC = 0.027
PAD_FRAC = 0.016
RADIUS_FRAC = 0.009
PILL_ALPHA = 150
MAX_LINES = 2


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(text: str, font, draw: ImageDraw.ImageDraw, max_width: int) -> list[str]:
    """Greedy word wrap by measured width, capped at MAX_LINES.

    Measured rather than character-counted: the caption is proportional type,
    so "illiterate" and "MMMMMMMMMM" are the same character length and nowhere
    near the same width.

    Text that runs past the cap ends in an ellipsis rather than being silently
    dropped. The 120-character limit at both write boundaries puts that out of
    reach in practice, but a caption quietly losing its second half is the
    kind of failure nobody notices until the clip is already shared.
    """
    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and draw.textlength(candidate, font=font) > max_width:
            lines.append(line)
            line = word
            if len(lines) == MAX_LINES:
                # No room for the line now being built, so the last one placed
                # has to admit that something follows it.
                lines[-1] = lines[-1] + " …"
                return lines
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def render_caption(text: str, width: int, height: int) -> Image.Image | None:
    """A frame-sized RGBA overlay carrying `text` in a pill, bottom-left.

    Returns None for an empty or whitespace-only note, so "no caption" stays a
    single check at the top of the pipeline rather than an overlay input that
    costs a composite to draw nothing.

    Frame-sized rather than pill-sized: the caller composites at 0,0, which
    keeps position a property of these pixels instead of an x/y expression
    threaded through the filter chain.

    Bottom-left because on this library's one camera position play sits
    mid-frame and the bottom strip is empty court. A full-width lower-third
    band was rejected for dimming any near-court action, and unboxed text for
    depending on whatever happens to be behind it -- floodlit night footage is
    the worst case and the common one.
    """
    text = text.strip()
    if not text:
        return None

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = int(height * MARGIN_FRAC)
    pad = int(height * PAD_FRAC)
    radius = int(height * RADIUS_FRAC)
    font = _load_font(int(height * FONT_FRAC))

    # Half the frame: a caption wider than that starts competing with the
    # picture, and two lines of it read faster than one very long one.
    lines = _wrap(text, font, draw, max_width=width // 2)

    line_h = int(height * FONT_FRAC * 1.35)
    text_w = max(int(draw.textlength(ln, font=font)) for ln in lines)
    text_h = line_h * len(lines)

    x0, y1 = margin, height - margin
    y0 = y1 - text_h - 2 * pad
    draw.rounded_rectangle(
        [x0, y0, x0 + text_w + 2 * pad, y1], radius=radius, fill=(0, 0, 0, PILL_ALPHA)
    )
    for i, line in enumerate(lines):
        draw.text((x0 + pad, y0 + pad + i * line_h), line, font=font, fill=(255, 255, 255, 255))

    return img
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_caption.py -v`
Expected: 6 passed.

- [ ] **Step 5: Look at one**

Render a caption over the real frame and view it, rather than trusting pixel assertions alone:

```bash
~/miniconda3/envs/splitstep/bin/python -c "
from PIL import Image
from splitstep.media.caption import render_caption
base = Image.open('/tmp/frame4k.png').convert('RGBA')
cap = render_caption('late on the backhand — good depth, bad recovery', *base.size)
Image.alpha_composite(base, cap).convert('RGB').resize((1280, 720)).save('/tmp/caption_check.png')
"
```

Expected: legible white text in a dark pill, bottom-left, clear of the player.

- [ ] **Step 6: Run the full suite and the linter, then commit**

```bash
~/miniconda3/envs/splitstep/bin/pytest -q && ~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
git add splitstep/media/caption.py tests/test_caption.py
git commit -m "feat(media): render a rally note to a caption overlay"
```

---

### Task 6: The caption fingerprint in the clip's name

**Files:**
- Modify: `splitstep/media/clips.py`
- Test: `tests/test_clips.py` (extend)

**Interfaces:**
- Consumes: nothing.
- Produces: `caption_fingerprint(note: str) -> str`; `clip_relpath(source_idx: int, start_ms: int, end_ms: int, note: str = "") -> str`; `parse_clip_name(name: str) -> tuple[int, int, int] | None` (return shape unchanged).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_clips.py`:

```python
from splitstep.media.clips import caption_fingerprint, clip_relpath, parse_clip_name


def test_an_uncaptioned_clip_keeps_the_name_it_has_always_had():
    # Every clip cut before notes existed must keep resolving, or the first
    # export after the migration re-cuts the lot.
    assert clip_relpath(1, 9000, 14000) == "01-9000-14000.mp4"
    assert clip_relpath(1, 9000, 14000, "") == "01-9000-14000.mp4"
    assert clip_relpath(1, 9000, 14000, "   ") == "01-9000-14000.mp4"


def test_a_captioned_clip_carries_the_note_in_its_name():
    name = clip_relpath(1, 9000, 14000, "late on the backhand")
    assert name.startswith("01-9000-14000-c")
    assert name.endswith(".mp4")


def test_a_changed_note_implies_a_different_file():
    # This is the whole staleness mechanism: a different path is a missing
    # path, and plan_export re-cuts what is missing.
    a = clip_relpath(1, 9000, 14000, "first")
    b = clip_relpath(1, 9000, 14000, "second")
    assert a != b


def test_the_fingerprint_is_stable_across_processes():
    # Filenames on disk outlive the process that wrote them, so this cannot be
    # hash() -- PYTHONHASHSEED randomizes it per run.
    assert caption_fingerprint("late on the backhand") == caption_fingerprint(
        "late on the backhand"
    )
    assert len(caption_fingerprint("x")) == 8
    assert caption_fingerprint("") == ""


def test_the_fingerprint_ignores_surrounding_whitespace():
    # Both write boundaries trim, so " x " and "x" are the same note and must
    # not resolve to two different files.
    assert caption_fingerprint("  x  ") == caption_fingerprint("x")


def test_both_name_forms_parse_back_to_source_and_span():
    assert parse_clip_name("01-9000-14000.mp4") == (1, 9000, 14000)
    captioned = clip_relpath(1, 9000, 14000, "note")
    assert parse_clip_name(captioned) == (1, 9000, 14000)


def test_a_name_with_an_unexpected_extra_segment_still_does_not_parse():
    # The regex stays anchored: a stranger's file in clips/ must never be
    # mistaken for one of ours and swept.
    assert parse_clip_name("01-9000-14000-cZZZZZZZZ.mp4") is None
    assert parse_clip_name("01-9000-14000-c1234.mp4") is None
    assert parse_clip_name("01-9000-14000-c12345678-extra.mp4") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_clips.py -v`
Expected: FAIL — `cannot import name 'caption_fingerprint'`.

- [ ] **Step 3: Implement**

In `splitstep/media/clips.py`:

```python
import hashlib
import re


def caption_fingerprint(note: str) -> str:
    """Eight hex characters identifying a note's text, '' for no note.

    SHA-256 rather than hash(): this value lands in filenames on disk, which
    outlive the process that wrote them, and PYTHONHASHSEED randomizes hash()
    per run -- every export would then re-cut every captioned clip.

    Stripped first, so the same note written with and without trailing
    whitespace resolves to one file. Both write boundaries already trim.
    """
    note = note.strip()
    if not note:
        return ""
    return hashlib.sha256(note.encode("utf-8")).hexdigest()[:8]
```

Extend `clip_relpath` (keep its existing docstring and add the caption paragraph):

```python
def clip_relpath(source_idx: int, start_ms: int, end_ms: int, note: str = "") -> str:
    """A clip's filename within its session's `clips/` directory.

    ... (existing docstring text unchanged) ...

    A note is part of the file's identity, not metadata about it: the caption
    is burned into the pixels, so two clips of the same span with different
    captions are different files. Encoding it here is what keeps staleness
    derived rather than recorded -- edit a note and the rally implies a path
    that does not exist, which plan_export already treats as "not cut yet" and
    find_orphan_clips already treats as "nothing claims the old file". The
    suffix is absent for an uncaptioned clip, so every clip cut before notes
    existed keeps resolving to its current name.
    """
    caption = caption_fingerprint(note)
    suffix = f"-c{caption}" if caption else ""
    return f"{source_idx:02d}-{start_ms}-{end_ms}{suffix}.mp4"


# Exactly what clip_relpath writes and nothing else: two digits of source
# index, two non-negative millisecond bounds, an optional caption fingerprint,
# ".mp4". Anchored at both ends so a name with an extra segment cannot match a
# prefix of it, and the fingerprint group is exactly eight lowercase hex so a
# stranger's file cannot slip in through it.
_CLIP_NAME = re.compile(r"^(\d{2,})-(\d+)-(\d+)(?:-c[0-9a-f]{8})?\.mp4$")
```

`parse_clip_name`'s body and return type are unchanged — the fingerprint group is non-capturing on purpose: orphan reporting names a file by source and span, and the hash would add nothing a human can act on.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_clips.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
git add splitstep/media/clips.py tests/test_clips.py
git commit -m "feat(clips): caption fingerprint in the clip filename"
```

---

### Task 7: Burn the caption in, and make export notice a changed note

**Files:**
- Modify: `splitstep/media/transcode.py` (`make_clip`)
- Modify: `splitstep/jobs/handlers.py` (`handle_clip`)
- Modify: `splitstep/export.py` (`plan_export`, `find_orphan_clips`)
- Test: `tests/test_transcode.py`, `tests/test_handler_clip.py`, `tests/test_export.py`, `tests/test_orphans.py` (extend)

**Interfaces:**
- Consumes: `render_caption` (Task 5); `clip_relpath(..., note=...)` (Task 6); `rallies.note` (Task 1).
- Produces: `make_clip(..., caption_png: Path | None = None)`; `clip` job payload gains `"note": str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_export.py`:

```python
def test_a_changed_note_puts_the_rally_back_in_the_pending_list(library, conn, seeded_clips):
    # The staleness rule, end to end: no column records that the clip is out of
    # date -- the rally simply implies a filename that is not on disk.
    plan = plan_export(library, conn, seeded_clips["session_id"], "points")
    assert plan.pending == []

    conn.execute("UPDATE rallies SET note = ? WHERE id = ?",
                 ("new caption", seeded_clips["rally_id"]))
    conn.commit()

    plan = plan_export(library, conn, seeded_clips["session_id"], "points")
    assert len(plan.pending) == 1
    assert plan.pending[0]["note"] == "new caption"


def test_the_pending_payload_carries_the_note_for_the_handler(library, conn, seeded_points):
    plan = plan_export(library, conn, seeded_points["session_id"], "points")
    assert all("note" in p for p in plan.pending)
```

Append to `tests/test_orphans.py`:

```python
def test_the_clip_a_note_replaced_is_reported_as_an_orphan(library, conn, seeded_clips):
    # The old caption's file stops being claimed the moment the note changes,
    # which is the same handling a moved boundary already gets.
    conn.execute("UPDATE rallies SET note = ? WHERE id = ?",
                 ("new caption", seeded_clips["rally_id"]))
    conn.commit()

    orphans = find_orphan_clips(library, conn, seeded_clips["session_id"])
    assert len(orphans) == 1
    assert orphans[0].path.name == seeded_clips["clip_name"]
```

Append to `tests/test_transcode.py` (follow whatever mechanism that file already uses to capture the ffmpeg argv — read it first and reuse it rather than inventing a second one):

```python
def test_a_caption_is_composited_after_pad_and_before_setsar(sample_video, tmp_path, caption_png):
    argv = capture_ffmpeg_argv(lambda: make_clip(
        sample_video, tmp_path / "out.mp4", start_ms=0, end_ms=1000,
        caption_png=caption_png,
    ))
    chain = argv[argv.index("-filter_complex") + 1]
    # Order is load-bearing: after pad, so the caption's coordinates are in
    # final 4K frame space and never get letterboxed with the picture; before
    # setsar, which the profile requires as the last filter.
    assert chain.index("pad=") < chain.index("overlay")
    assert chain.index("overlay") < chain.index("setsar=1")


def test_the_caption_input_is_looped_so_it_covers_the_whole_clip(
    sample_video, tmp_path, caption_png
):
    # A PNG is one frame. Without -loop 1 the overlay applies to frame one and
    # the caption vanishes for the rest of the clip.
    argv = capture_ffmpeg_argv(lambda: make_clip(
        sample_video, tmp_path / "out.mp4", start_ms=0, end_ms=1000,
        caption_png=caption_png,
    ))
    assert argv[argv.index(str(caption_png)) - 1] == "-i"
    assert "-loop" in argv


def test_no_caption_leaves_the_command_exactly_as_it_was(sample_video, tmp_path):
    argv = capture_ffmpeg_argv(lambda: make_clip(
        sample_video, tmp_path / "out.mp4", start_ms=0, end_ms=1000,
    ))
    # The uncaptioned path must not switch to -filter_complex: every clip
    # already on disk was cut by the -vf path, and a different filter graph is
    # a different encode to have to reason about.
    assert "-vf" in argv
    assert "-filter_complex" not in argv


def test_a_captioned_clip_still_carries_audio(sample_video, tmp_path, caption_png):
    # The caption is a third input on a silent source. Stream indices shift,
    # and getting the mapping wrong drops the audio track -- which breaks
    # -c copy concat against every clip that has one.
    out = tmp_path / "out.mp4"
    make_clip(sample_video, out, start_ms=0, end_ms=1000, caption_png=caption_png)
    assert probe(out).has_audio
```

Append to `tests/test_handler_clip.py`:

```python
def test_the_handler_burns_the_rallys_note_into_the_clip(library, conn, clip_job_source):
    # Renders through the real Pillow path; no ffmpeg text filter is involved,
    # which is the point of caption.py existing.
    handle_clip(library, {**clip_job_source["payload"], "note": "late on the backhand"})
    expected = clip_relpath(
        clip_job_source["source_idx"],
        clip_job_source["start_ms"],
        clip_job_source["end_ms"],
        "late on the backhand",
    )
    assert (library.clips_dir(clip_job_source["session_id"]) / expected).exists()


def test_a_note_less_rally_cuts_to_the_plain_name(library, conn, clip_job_source):
    handle_clip(library, {**clip_job_source["payload"], "note": ""})
    expected = clip_relpath(
        clip_job_source["source_idx"],
        clip_job_source["start_ms"],
        clip_job_source["end_ms"],
    )
    assert (library.clips_dir(clip_job_source["session_id"]) / expected).exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_transcode.py tests/test_handler_clip.py tests/test_export.py tests/test_orphans.py -v`
Expected: FAIL — `make_clip() got an unexpected keyword argument 'caption_png'`, and KeyError on `note` in the export payloads.

- [ ] **Step 3: Add `caption_png` to `make_clip`**

In `splitstep/media/transcode.py`, add the parameter to the signature:

```python
    caption_png: Path | None = None,
```

Keep the existing `vf` construction, but drop `setsar=1` from the tuple into its own variable so both paths can place it last:

```python
    chain = ",".join(
        f
        for f in (
            unsquish,
            rotation_filter(rotation_deg),
            f"scale={CLIP_WIDTH}:{CLIP_HEIGHT}:force_original_aspect_ratio=decrease",
            f"pad={CLIP_WIDTH}:{CLIP_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
        )
        if f
    )
    vf = f"{chain},setsar=1"
```

Then build the caption inputs and the filter arguments:

```python
    # The caption is composited AFTER pad, so its coordinates are in final 4K
    # frame space -- render_caption draws at the frame's size and the overlay
    # sits at 0,0 -- and BEFORE setsar, which the profile requires last.
    #
    # -loop 1 because a PNG is a single frame: without it the overlay applies
    # to the first frame and the caption disappears for the rest of the clip.
    # The output's -t is what bounds the otherwise infinite image stream, the
    # same way it bounds anullsrc above.
    #
    # -filter_complex rather than -vf only on this path. The uncaptioned path
    # keeps the exact command every clip already on disk was cut with; a
    # second input cannot be referenced from -vf at all.
    caption_in: list[str] = []
    filter_args = ["-vf", vf]
    if caption_png is not None:
        caption_idx = 2 if not info.has_audio else 1
        caption_in = ["-loop", "1", "-i", str(caption_png)]
        filter_args = [
            "-filter_complex",
            f"[0:v]{chain}[base];[base][{caption_idx}:v]overlay=0:0,setsar=1[vout]",
        ]
        # Explicit mapping becomes mandatory with the extra input: ffmpeg's
        # default selection cannot pick a labelled filtergraph output, and the
        # audio it would guess at is now one input further along.
        audio_src = "1:a:0" if not info.has_audio else "0:a:0"
        mapping = ["-map", "[vout]", "-map", audio_src]
```

Add `*caption_in` to the argv immediately after `*silence`, and replace the `"-vf", vf,` entry with `*filter_args`.

- [ ] **Step 4: Wire the handler and the planner**

In `splitstep/jobs/handlers.py::handle_clip`, after the `start_ms, end_ms` line:

```python
    note = payload.get("note", "")
    name = clip_relpath(source["idx"], start_ms, end_ms, note)
```

and, before `make_clip`:

```python
    # Rendered to a temp PNG beside the clip rather than held in memory:
    # ffmpeg reads it as an input, so it has to exist on disk for the length
    # of the encode. Dot-prefixed and uuid-suffixed for the same reason
    # make_clip's own temp file is -- clips_dir is swept by name, and a stray
    # PNG must never look like a clip.
    caption_png = None
    caption = render_caption(note, CLIP_WIDTH, CLIP_HEIGHT)
    if caption is not None:
        caption_png = dst.with_name(f".{dst.stem}.{uuid.uuid4().hex}.caption.png")
        caption.save(caption_png)
    try:
        make_clip(src, dst, start_ms=start_ms, end_ms=end_ms,
                  rotation_deg=source["rotation_deg"], on_progress=progress,
                  caption_png=caption_png)
    finally:
        if caption_png is not None:
            caption_png.unlink(missing_ok=True)
```

In `splitstep/export.py::plan_export`, pass the note into the name and into the payload:

```python
        name = clip_relpath(source["idx"], rally["start_ms"], rally["end_ms"], rally["note"])
```

```python
        pending.append({
            "source_id": rally["source_id"],
            "rally_id": rally["id"],
            "start_ms": rally["start_ms"],
            "end_ms": rally["end_ms"],
            "note": rally["note"],
        })
```

In `find_orphan_clips`, the claimed set must include the note, or every captioned clip reads as an orphan the moment it is cut:

```python
    claimed = {
        clip_relpath(row["idx"], row["start_ms"], row["end_ms"], row["note"])
        for row in conn.execute(
            "SELECT s.idx AS idx, r.start_ms AS start_ms, r.end_ms AS end_ms,"
            " r.note AS note"
            " FROM rallies r JOIN sources s ON s.id = r.source_id"
            " WHERE r.session_id = ?",
            (session_id,),
        )
    }
```

Add `render_caption` and `CLIP_WIDTH`/`CLIP_HEIGHT` to `handlers.py`'s imports.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_transcode.py tests/test_handler_clip.py tests/test_export.py tests/test_orphans.py -v`
Expected: all pass.

- [ ] **Step 6: Run the full suite and the linter**

Run: `~/miniconda3/envs/splitstep/bin/pytest -q && ~/miniconda3/envs/splitstep/bin/ruff check splitstep tests`
Expected: green.

- [ ] **Step 7: Verify concat compatibility by hand**

The one property no unit test protects. Cut one captioned and one uncaptioned clip from the real library, then:

```bash
printf "file '%s'\nfile '%s'\n" /path/to/captioned.mp4 /path/to/plain.mp4 > /tmp/concat.txt
ffmpeg -f concat -safe 0 -i /tmp/concat.txt -c copy /tmp/joined.mp4
```

Expected: exits 0 with no "Non-monotonous DTS" or parameter-mismatch warnings, and the joined file plays with the caption visible on the first clip only.

- [ ] **Step 8: Update CLAUDE.md and commit**

CLAUDE.md's "Deferred" section says 4K clip export shipped; add a line to the conventions section recording that a caption is part of a clip's filename identity and why, so the next reader does not "tidy" the suffix away.

```bash
git add splitstep/media/transcode.py splitstep/jobs/handlers.py splitstep/export.py tests CLAUDE.md
git commit -m "feat(clips): burn a rally's note into its exported clip"
```

---

## Self-review notes

**Spec coverage.** §3 capture → Task 4 (`N`, Enter/Escape, ✎, 120 cap) and Task 3 (`lib/notes.ts`). §4 storage → Task 1. §5 burn-in → Tasks 5 and 7. §6 staleness → Tasks 6 and 7. §7 sequencing → the phase split and the Task 5 gate. §8 testing → every task's test step; the by-hand concat check is Task 7 Step 7.

**Known gap, deliberate.** The spec's §3 says the route answers 400; this plan uses 422 and says why at the top. No other divergence.

**Type consistency.** `clip_relpath`'s `note` parameter is keyword-optional with the same default (`""`) at all four call sites (Task 6 definition; Task 7's `handle_clip`, `plan_export`, `find_orphan_clips`). `render_caption(text, width, height)` is called with `CLIP_WIDTH, CLIP_HEIGHT` in Task 7 and with a frame's own size in Task 5's by-hand check. `NOTE_MAX_CHARS` is 120 in both `splitstep/db/rallies.py` and `web/src/lib/notes.ts`.
