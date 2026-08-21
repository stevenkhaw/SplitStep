# Rally Labelling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn ordinary rally review into a durable labelled corpus, and give `segment()` a numeric target to be scored against.

**Architecture:** A new append-only `rally_labels` table anchored to `(source_id, span_start_ms, span_end_ms)` — the detector's own span — rather than to a rally row, so labels survive `replace_rallies`. Two write paths feed it: a new keyboard-driven label mode in the SPA, and the existing bounds route, which turns every boundary drag into a signed millisecond correction for free. A pure scorer plus two CLI subcommands close the loop.

**Tech Stack:** Python 3.12 (sqlite3, FastAPI, pydantic, pytest), Svelte 5 runes + TypeScript + vitest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-21-rally-labelling-design.md`. Read it before Task 1.
- Python runs from the `bootleg` conda env by path: `~/miniconda3/envs/bootleg/bin/pytest`, `~/miniconda3/envs/bootleg/bin/ruff`.
- ruff line-length is 100.
- `pytest` runs with `filterwarnings = ["error"]` — a new warning fails the suite.
- Migrations are numbered `.sql` files applied by `PRAGMA user_version`. Add `003_rally_labels.sql`; never edit `001_init.sql` or `002_rotation.sql`.
- Comments explain **why**, not what. This codebase carries long rationale comments on non-obvious calls. Match that density.
- All frontend logic goes in `web/src/lib/*.ts`, never in a `.svelte` file — jsdom has no `<video>`, so anything inside a component is untestable.
- YOLO is never run in tests. Nothing in this plan touches the detector's expensive stage.
- Frontend commands run from `web/`: `npm run check`, `npx vitest run`.

---

### Task 1: `rally_labels` table and its data module

**Files:**
- Create: `bootleg/db/migrations/003_rally_labels.sql`
- Create: `bootleg/db/labels.py`
- Test: `tests/test_labels.py`

**Interfaces:**
- Consumes: `bootleg.db.schema.migrate`, `bootleg.db.rallies.replace_rallies` (test only).
- Produces:
  - `VERDICTS: tuple[str, ...]`, `FLAG_ORDER: tuple[str, ...]`
  - `format_flags(flags: Iterable[str]) -> str`
  - `parse_flags(raw: str) -> list[str]`
  - `add_label(conn, *, source_id: str, span_start_ms: int, span_end_ms: int, verdict: str | None = None, boundary_flags: Iterable[str] = (), true_start_ms: int | None = None, true_end_ms: int | None = None, rally_id: str | None = None) -> str`
  - `latest_labels(conn, source_id: str) -> list[sqlite3.Row]`
  - `record_boundary_correction(conn, *, rally_id: str, source_id: str, det_start_ms: int, det_end_ms: int, true_start_ms: int, true_end_ms: int) -> str | None`

- [ ] **Step 1: Write the migration**

Create `bootleg/db/migrations/003_rally_labels.sql`:

```sql
-- Human judgements about spans of a source, kept as a corpus for scoring the
-- detector. Deliberately NOT columns on `rallies`: replace_rallies rewrites
-- every rally row on each re-segment, so anything living there is either
-- destroyed or carried across by overlap into a row whose boundaries have
-- since moved -- which would make a flag like start_late a statement about
-- boundaries that no longer exist.
CREATE TABLE rally_labels (
  id             TEXT PRIMARY KEY,
  source_id      TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,

  -- The detector span that was judged: always the rally's det_start_ms /
  -- det_end_ms, never the human-edited start_ms / end_ms. This is what
  -- anchors a label to the source timeline rather than to a row, and it is
  -- why a label stays meaningful after any number of re-segments.
  span_start_ms  INTEGER NOT NULL,
  span_end_ms    INTEGER NOT NULL,

  verdict        TEXT CHECK (verdict IS NULL OR
                             verdict IN ('clean','not_play','partly','unsure')),

  -- Comma-separated subset of start_early,start_late,end_early,end_late in
  -- that fixed order. Empty string when none. The canonical order keeps an
  -- exported fixture byte-stable across re-exports, the same reason
  -- features.jsonl quantizes its floats.
  boundary_flags TEXT NOT NULL DEFAULT '',

  -- The human-corrected span, when the reviewer actually dragged the
  -- handles. NULL on a verdict-only row.
  true_start_ms  INTEGER,
  true_end_ms    INTEGER,

  -- Provenance only, and DELIBERATELY NOT A FOREIGN KEY. replace_rallies
  -- runs `DELETE FROM rallies WHERE source_id = ?` on every re-segment; a
  -- REFERENCES rallies(id) ON DELETE CASCADE here would cascade that delete
  -- across the whole corpus on the first threshold sweep. That is the exact
  -- failure this table exists to avoid -- do not "fix" the missing FK.
  rally_id       TEXT,

  labelled_at    TEXT NOT NULL,

  -- A row must carry a verdict, a corrected span, or both. A boundary drag
  -- asserts that the edges were wrong and supplies the right ones; it does
  -- not assert a verdict, so verdict stays nullable -- but an empty row is
  -- never meaningful.
  CHECK (verdict IS NOT NULL OR true_start_ms IS NOT NULL)
);

CREATE INDEX idx_rally_labels_source ON rally_labels(source_id, span_start_ms);
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_labels.py`:

```python
import pytest
import sqlite3

from bootleg.db.labels import (
    add_label,
    format_flags,
    latest_labels,
    parse_flags,
    record_boundary_correction,
)
from bootleg.db.rallies import replace_rallies
from bootleg.db.sessions import add_source, find_or_create_session_for_date
from bootleg.detect.segment import Interval


@pytest.fixture
def seeded(conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, _idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=1920, height=1080, fps=30.0, original_name="IMG_9000.MOV",
    )
    return {"session_id": session_id, "source_id": source_id}


def test_format_flags_uses_canonical_order_regardless_of_input_order(seeded):
    assert format_flags(["end_late", "start_early"]) == "start_early,end_late"
    assert format_flags([]) == ""


def test_format_flags_rejects_an_unknown_flag(seeded):
    with pytest.raises(ValueError, match="unknown boundary flag"):
        format_flags(["start_slightly_early"])


def test_parse_flags_round_trips_and_maps_empty_to_no_flags(seeded):
    assert parse_flags("start_early,end_late") == ["start_early", "end_late"]
    assert parse_flags("") == []


def test_add_label_stores_a_verdict_row(conn, seeded):
    add_label(conn, source_id=seeded["source_id"], span_start_ms=1000,
              span_end_ms=5000, verdict="clean")
    rows = latest_labels(conn, seeded["source_id"])
    assert len(rows) == 1
    assert rows[0]["verdict"] == "clean"
    assert rows[0]["boundary_flags"] == ""
    assert rows[0]["true_start_ms"] is None


def test_add_label_rejects_an_unknown_verdict(conn, seeded):
    with pytest.raises(ValueError, match="unknown verdict"):
        add_label(conn, source_id=seeded["source_id"], span_start_ms=1000,
                  span_end_ms=5000, verdict="probably")


def test_add_label_rejects_a_row_carrying_neither_verdict_nor_corrected_span(conn, seeded):
    # The table-level CHECK. Unreachable from HTTP (the route requires a
    # verdict) but a programming error here should fail loudly, not insert junk.
    with pytest.raises(sqlite3.IntegrityError):
        add_label(conn, source_id=seeded["source_id"], span_start_ms=1000,
                  span_end_ms=5000, verdict=None)


def test_relabelling_a_span_appends_and_the_latest_row_wins(conn, seeded):
    add_label(conn, source_id=seeded["source_id"], span_start_ms=1000,
              span_end_ms=5000, verdict="not_play")
    add_label(conn, source_id=seeded["source_id"], span_start_ms=1000,
              span_end_ms=5000, verdict="clean")

    # Both rows survive -- clip #1's hand label was wrong once and the
    # correction was itself the finding, so history is never overwritten.
    total = conn.execute("SELECT COUNT(*) FROM rally_labels").fetchone()[0]
    assert total == 2

    rows = latest_labels(conn, seeded["source_id"])
    assert len(rows) == 1
    assert rows[0]["verdict"] == "clean"


def test_latest_labels_breaks_a_labelled_at_tie_by_insertion_order(conn, seeded):
    # Two labels written inside the same clock tick must still resolve
    # deterministically to the later insert, not to whichever row sqlite
    # happens to return first.
    conn.execute(
        "INSERT INTO rally_labels (id,source_id,span_start_ms,span_end_ms,verdict,"
        "boundary_flags,labelled_at) VALUES ('a',?,1000,5000,'not_play','','T')",
        (seeded["source_id"],),
    )
    conn.execute(
        "INSERT INTO rally_labels (id,source_id,span_start_ms,span_end_ms,verdict,"
        "boundary_flags,labelled_at) VALUES ('b',?,1000,5000,'clean','','T')",
        (seeded["source_id"],),
    )
    conn.commit()
    rows = latest_labels(conn, seeded["source_id"])
    assert [r["verdict"] for r in rows] == ["clean"]


def test_distinct_spans_each_keep_their_own_latest_row(conn, seeded):
    add_label(conn, source_id=seeded["source_id"], span_start_ms=1000,
              span_end_ms=5000, verdict="clean")
    add_label(conn, source_id=seeded["source_id"], span_start_ms=9000,
              span_end_ms=14000, verdict="not_play")
    rows = latest_labels(conn, seeded["source_id"])
    assert [(r["span_start_ms"], r["verdict"]) for r in rows] == [
        (1000, "clean"), (9000, "not_play")
    ]


def test_the_corpus_survives_replace_rallies(conn, seeded):
    """The load-bearing property. Approach B exists entirely for this.

    If anyone ever adds `REFERENCES rallies(id) ON DELETE CASCADE` to
    rally_labels.rally_id, replace_rallies' DELETE will wipe the corpus and
    this test is what catches it.
    """
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    rally_id = conn.execute("SELECT id FROM rallies").fetchone()["id"]
    add_label(conn, source_id=seeded["source_id"], span_start_ms=1000,
              span_end_ms=5000, verdict="clean", rally_id=rally_id)

    # A threshold sweep: every rally row for the source is deleted and rebuilt.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1200, 4800, 0.7), Interval(20000, 26000, 0.6)])

    rows = latest_labels(conn, seeded["source_id"])
    assert len(rows) == 1
    assert rows[0]["verdict"] == "clean"
    assert rows[0]["span_start_ms"] == 1000


def test_record_boundary_correction_derives_start_early_and_end_late(conn, seeded):
    # Detector opened at 1000 but play began at 1400 -> it started early.
    # Detector closed at 5000 but play ended at 4600 -> it ran on, i.e. late.
    label_id = record_boundary_correction(
        conn, rally_id="r1", source_id=seeded["source_id"],
        det_start_ms=1000, det_end_ms=5000,
        true_start_ms=1400, true_end_ms=4600,
    )
    assert label_id is not None
    row = latest_labels(conn, seeded["source_id"])[0]
    assert row["verdict"] is None
    assert parse_flags(row["boundary_flags"]) == ["start_early", "end_late"]
    assert (row["true_start_ms"], row["true_end_ms"]) == (1400, 4600)


def test_record_boundary_correction_derives_start_late_and_end_early(conn, seeded):
    # The mirror case: detector opened after play began and closed before it ended.
    record_boundary_correction(
        conn, rally_id="r1", source_id=seeded["source_id"],
        det_start_ms=1000, det_end_ms=5000,
        true_start_ms=600, true_end_ms=5400,
    )
    row = latest_labels(conn, seeded["source_id"])[0]
    assert parse_flags(row["boundary_flags"]) == ["start_late", "end_early"]


def test_record_boundary_correction_writes_nothing_when_the_span_is_unchanged(conn, seeded):
    # A drag that went nowhere is not a correction.
    label_id = record_boundary_correction(
        conn, rally_id="r1", source_id=seeded["source_id"],
        det_start_ms=1000, det_end_ms=5000,
        true_start_ms=1000, true_end_ms=5000,
    )
    assert label_id is None
    assert latest_labels(conn, seeded["source_id"]) == []


def test_record_boundary_correction_flags_only_the_edge_that_moved(conn, seeded):
    record_boundary_correction(
        conn, rally_id="r1", source_id=seeded["source_id"],
        det_start_ms=1000, det_end_ms=5000,
        true_start_ms=1000, true_end_ms=4600,
    )
    row = latest_labels(conn, seeded["source_id"])[0]
    assert parse_flags(row["boundary_flags"]) == ["end_late"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_labels.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'bootleg.db.labels'`

- [ ] **Step 4: Write `bootleg/db/labels.py`**

```python
import sqlite3
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

VERDICTS = ("clean", "not_play", "partly", "unsure")

# Fixed order, not the caller's. An exported fixture is committed to
# tests/fixtures and re-exported later; a set-ordered join would produce diff
# noise on every export for no change in content. Same reasoning as
# features.jsonl quantizing its floats to 4dp for byte-stable round trips.
FLAG_ORDER = ("start_early", "start_late", "end_early", "end_late")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def format_flags(flags: Iterable[str]) -> str:
    present = set(flags)
    unknown = present - set(FLAG_ORDER)
    if unknown:
        raise ValueError(f"unknown boundary flag(s): {sorted(unknown)}")
    return ",".join(f for f in FLAG_ORDER if f in present)


def parse_flags(raw: str) -> list[str]:
    # "".split(",") is [""], not [] -- filter, do not rstrip-and-split.
    return [f for f in raw.split(",") if f]


def add_label(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    span_start_ms: int,
    span_end_ms: int,
    verdict: str | None = None,
    boundary_flags: Iterable[str] = (),
    true_start_ms: int | None = None,
    true_end_ms: int | None = None,
    rally_id: str | None = None,
) -> str:
    """Append one human judgement about a span of a source.

    `span_start_ms`/`span_end_ms` must be the detector's own guess
    (`det_start_ms`/`det_end_ms`), never the human-edited bounds -- that is
    what keeps a label meaningful across re-segments.

    Never updates. Re-labelling the same span appends another row and
    `latest_labels` resolves which one is current, so a corrected judgement
    never erases the one it corrected.
    """
    if verdict is not None and verdict not in VERDICTS:
        raise ValueError(f"unknown verdict: {verdict!r}")

    label_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO rally_labels (id,source_id,span_start_ms,span_end_ms,verdict,"
        "boundary_flags,true_start_ms,true_end_ms,rally_id,labelled_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (label_id, source_id, span_start_ms, span_end_ms, verdict,
         format_flags(boundary_flags), true_start_ms, true_end_ms, rally_id, _now()),
    )
    conn.commit()
    return label_id


def latest_labels(conn: sqlite3.Connection, source_id: str) -> list[sqlite3.Row]:
    """The current judgement for each distinct span of a source.

    The tie-break on `rowid DESC` is not decoration: two labels written inside
    the same clock tick share a `labelled_at`, and without it sqlite would be
    free to return either. Higher rowid is the later INSERT, which is the
    later judgement.
    """
    return conn.execute(
        "SELECT * FROM ("
        "  SELECT *, ROW_NUMBER() OVER ("
        "           PARTITION BY span_start_ms, span_end_ms"
        "           ORDER BY labelled_at DESC, rowid DESC) AS rn"
        "    FROM rally_labels WHERE source_id = ?"
        ") WHERE rn = 1 ORDER BY span_start_ms",
        (source_id,),
    ).fetchall()


def record_boundary_correction(
    conn: sqlite3.Connection,
    *,
    rally_id: str,
    source_id: str,
    det_start_ms: int,
    det_end_ms: int,
    true_start_ms: int,
    true_end_ms: int,
) -> str | None:
    """Turn a manual boundary drag into a boundary-bearing label.

    Returns the new label id, or None when the saved span equals the
    detector's -- a drag that went nowhere is not a correction.

    `verdict` stays NULL on purpose. Dragging the handles asserts that the
    edges were wrong and supplies the right ones; it does not assert that the
    span contains a rally, and defaulting it to 'clean' would fabricate a
    judgement the reviewer never made.
    """
    if (true_start_ms, true_end_ms) == (det_start_ms, det_end_ms):
        return None

    flags: list[str] = []
    # Detector opened before play began -> it started early, and vice versa.
    if true_start_ms > det_start_ms:
        flags.append("start_early")
    elif true_start_ms < det_start_ms:
        flags.append("start_late")
    # Detector ran on past the end -> it ended late, and vice versa.
    if true_end_ms < det_end_ms:
        flags.append("end_late")
    elif true_end_ms > det_end_ms:
        flags.append("end_early")

    return add_label(
        conn, source_id=source_id, span_start_ms=det_start_ms, span_end_ms=det_end_ms,
        verdict=None, boundary_flags=flags,
        true_start_ms=true_start_ms, true_end_ms=true_end_ms, rally_id=rally_id,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_labels.py -q`
Expected: PASS, 14 passed

- [ ] **Step 6: Run the full suite and the linter**

Run: `~/miniconda3/envs/bootleg/bin/pytest -q && ~/miniconda3/envs/bootleg/bin/ruff check bootleg tests`
Expected: all tests pass (348 existing + 14 new), ruff clean

- [ ] **Step 7: Commit**

```bash
git add bootleg/db/migrations/003_rally_labels.sql bootleg/db/labels.py tests/test_labels.py
git commit -m "feat(db): an append-only label corpus anchored to detector spans

Anchored to (source_id, span_start_ms, span_end_ms) rather than to a rally
row, because replace_rallies rewrites every rally on each threshold sweep.
rally_id deliberately carries no foreign key -- a cascade from that DELETE
would wipe the corpus. test_the_corpus_survives_replace_rallies is what
catches anyone adding it back."
```

---

### Task 2: Label API routes

**Files:**
- Modify: `bootleg/api/routes.py` (imports at 12-20, body models near 42-56, new routes after `api_bounds` at 169-173)
- Modify: `docs/superpowers/specs/2026-08-21-rally-labelling-design.md` (§8, first bullet group)
- Test: `tests/test_api_labels.py`

**Interfaces:**
- Consumes: `add_label`, `latest_labels`, `parse_flags`, `VERDICTS`, `FLAG_ORDER` from Task 1.
- Produces:
  - `POST /api/rallies/{rally_id}/label` — body `{"verdict": str, "boundary_flags": [str]}` → `{"ok": true, "id": str}`
  - `GET /api/sources/{source_id}/labels` → `[{span_start_ms, span_end_ms, verdict, boundary_flags, true_start_ms, true_end_ms, labelled_at}]`
  - `_rally_det_span(conn, rally_id) -> sqlite3.Row` (module-private, reused by Task 3)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_labels.py`:

```python
import pytest
from fastapi.testclient import TestClient

from bootleg.api.app import create_app
from bootleg.db.labels import add_label
from bootleg.db.rallies import replace_rallies
from bootleg.db.sessions import add_source, find_or_create_session_for_date
from bootleg.detect.segment import Interval


@pytest.fixture
def client(library, conn):
    # Lifespan startup/shutdown only run inside the context manager, and
    # shutdown is what closes the per-thread connection pool.
    with TestClient(create_app(library)) as c:
        yield c


@pytest.fixture
def seeded(library, conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=1920, height=1080, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7)])
    return {"session_id": session_id, "source_id": source_id, "idx": idx}


def _first_rally(conn):
    return conn.execute("SELECT * FROM rallies ORDER BY idx").fetchone()


def test_label_route_anchors_to_the_detector_span_not_the_edited_bounds(client, conn, seeded):
    rally = _first_rally(conn)
    client.post(f"/api/rallies/{rally['id']}/bounds",
                json={"start_ms": 1400, "end_ms": 4600})

    r = client.post(f"/api/rallies/{rally['id']}/label",
                    json={"verdict": "clean", "boundary_flags": []})
    assert r.status_code == 200

    rows = client.get(f"/api/sources/{seeded['source_id']}/labels").json()
    verdict_rows = [row for row in rows if row["verdict"] == "clean"]
    assert len(verdict_rows) == 1
    # det span, not the 1400/4600 the reviewer dragged to.
    assert verdict_rows[0]["span_start_ms"] == 1000
    assert verdict_rows[0]["span_end_ms"] == 5000


def test_label_route_stores_boundary_flags(client, conn, seeded):
    rally = _first_rally(conn)
    client.post(f"/api/rallies/{rally['id']}/label",
                json={"verdict": "partly", "boundary_flags": ["end_late", "start_early"]})

    rows = client.get(f"/api/sources/{seeded['source_id']}/labels").json()
    assert rows[0]["boundary_flags"] == ["start_early", "end_late"]


def test_label_route_rejects_an_unknown_verdict(client, conn, seeded):
    rally = _first_rally(conn)
    r = client.post(f"/api/rallies/{rally['id']}/label",
                    json={"verdict": "probably", "boundary_flags": []})
    assert r.status_code == 422


def test_label_route_rejects_an_unknown_boundary_flag(client, conn, seeded):
    rally = _first_rally(conn)
    r = client.post(f"/api/rallies/{rally['id']}/label",
                    json={"verdict": "clean", "boundary_flags": ["start_slightly_early"]})
    assert r.status_code == 422


def test_label_route_404s_on_a_rally_a_resegment_deleted(client, conn, seeded):
    rally = _first_rally(conn)
    replace_rallies(conn, seeded["session_id"], seeded["source_id"], [Interval(2000, 6000, 0.9)])
    r = client.post(f"/api/rallies/{rally['id']}/label",
                    json={"verdict": "clean", "boundary_flags": []})
    assert r.status_code == 404


def test_source_labels_returns_only_the_latest_row_per_span(client, conn, seeded):
    rally = _first_rally(conn)
    client.post(f"/api/rallies/{rally['id']}/label",
                json={"verdict": "not_play", "boundary_flags": []})
    client.post(f"/api/rallies/{rally['id']}/label",
                json={"verdict": "clean", "boundary_flags": []})

    rows = client.get(f"/api/sources/{seeded['source_id']}/labels").json()
    assert len(rows) == 1
    assert rows[0]["verdict"] == "clean"


def test_source_labels_reports_a_boundary_only_row_with_a_null_verdict(client, conn, seeded):
    add_label(conn, source_id=seeded["source_id"], span_start_ms=1000, span_end_ms=5000,
              verdict=None, boundary_flags=["end_late"], true_start_ms=1000,
              true_end_ms=4600)
    rows = client.get(f"/api/sources/{seeded['source_id']}/labels").json()
    assert rows[0]["verdict"] is None
    assert rows[0]["true_end_ms"] == 4600


def test_source_labels_404s_on_an_unknown_source(client, seeded):
    r = client.get("/api/sources/nope/labels")
    assert r.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_api_labels.py -q`
Expected: FAIL — the label routes return 404/405 and `test_source_labels_404s_on_an_unknown_source` is the only one that could accidentally pass.

- [ ] **Step 3: Add the imports and body model**

In `bootleg/api/routes.py`, add after the `bootleg.db.presets` import (line 11):

```python
from bootleg.db.labels import FLAG_ORDER, VERDICTS, add_label, latest_labels, parse_flags
```

Add after `class BoundsBody` (ends line 62):

```python
class LabelBody(BaseModel):
    # `verdict` is required here even though the column is nullable. The only
    # writer of a verdict-less row is the bounds route (Task 3), which derives
    # everything server-side; an HTTP client asserting nothing at all would be
    # writing an empty judgement.
    verdict: str
    boundary_flags: list[str] = Field(default_factory=list)

    @field_validator("verdict")
    @classmethod
    def check_verdict(cls, v: str) -> str:
        if v not in VERDICTS:
            raise ValueError(f"verdict must be one of {list(VERDICTS)}")
        return v

    @field_validator("boundary_flags")
    @classmethod
    def check_flags(cls, v: list[str]) -> list[str]:
        unknown = set(v) - set(FLAG_ORDER)
        if unknown:
            raise ValueError(f"unknown boundary flag(s): {sorted(unknown)}")
        return v
```

- [ ] **Step 4: Add the helper and the two routes**

In `bootleg/api/routes.py`, add next to `_session_id_for_rally` (line 136):

```python
def _rally_det_span(conn, rally_id: str):
    """The rally's immutable detector span, or 404.

    Every label anchors to det_start_ms/det_end_ms rather than the editable
    start_ms/end_ms, so this is resolved server-side and clients never send a
    span -- a client that computed it from stale rally data could otherwise
    anchor a judgement to a span the detector never produced.
    """
    row = conn.execute(
        "SELECT source_id, det_start_ms, det_end_ms FROM rallies WHERE id = ?",
        (rally_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Rally not found")
    return row
```

Add after `api_bounds` (line 173):

```python
@router.post("/api/rallies/{rally_id}/label")
def api_label(rally_id: str, body: LabelBody, request: Request):
    conn = _conn(request)
    span = _rally_det_span(conn, rally_id)
    label_id = add_label(
        conn,
        source_id=span["source_id"],
        span_start_ms=span["det_start_ms"],
        span_end_ms=span["det_end_ms"],
        verdict=body.verdict,
        boundary_flags=body.boundary_flags,
        rally_id=rally_id,
    )
    # No session_status refresh: a label is a note about the detector, not a
    # review decision, and flipping a session to 'reviewed' because someone
    # labelled one clip would misreport the review pass.
    return {"ok": True, "id": label_id}


@router.get("/api/sources/{source_id}/labels")
def api_source_labels(source_id: str, request: Request):
    conn = _conn(request)
    if get_source(conn, source_id) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return [
        {
            "span_start_ms": r["span_start_ms"],
            "span_end_ms": r["span_end_ms"],
            "verdict": r["verdict"],
            "boundary_flags": parse_flags(r["boundary_flags"]),
            "true_start_ms": r["true_start_ms"],
            "true_end_ms": r["true_end_ms"],
            "labelled_at": r["labelled_at"],
        }
        for r in latest_labels(conn, source_id)
    ]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_api_labels.py -q`
Expected: PASS, 8 passed

- [ ] **Step 6: Correct the spec's error-code claim**

§8 of the spec says a `CHECK` violation surfaces as 400. That is not what was built and not what should be: pydantic validates `verdict` and `boundary_flags` before any SQL runs and returns FastAPI's standard 422, and the table-level `CHECK` is unreachable from HTTP because the route requires a verdict.

In `docs/superpowers/specs/2026-08-21-rally-labelling-design.md`, replace this bullet:

```markdown
- A `CHECK` violation surfaces as 400, not 500.
```

with:

```markdown
- A malformed body — unknown verdict, unknown boundary flag — is rejected by
  pydantic before any SQL runs, giving FastAPI's standard 422. The table-level
  `CHECK` is unreachable over HTTP: `LabelBody.verdict` is required, so no
  request can produce a row carrying neither a verdict nor a corrected span.
  `add_label` still validates the verdict itself, for the CLI path.
```

- [ ] **Step 7: Run the full suite and the linter**

Run: `~/miniconda3/envs/bootleg/bin/pytest -q && ~/miniconda3/envs/bootleg/bin/ruff check bootleg tests`
Expected: all pass, ruff clean

- [ ] **Step 8: Commit**

```bash
git add bootleg/api/routes.py tests/test_api_labels.py \
        docs/superpowers/specs/2026-08-21-rally-labelling-design.md
git commit -m "feat(api): label a rally, and read a source's labels

The span is resolved server-side from det_start_ms/det_end_ms so a client
holding stale rally data cannot anchor a judgement to a span the detector
never produced. Corrects the spec's claim that a CHECK violation returns
400: pydantic rejects a bad body with 422 before any SQL runs, and the
table-level CHECK is unreachable over HTTP."
```

---

### Task 3: The bounds route records a boundary correction

**Files:**
- Modify: `bootleg/api/routes.py:169-173` (`api_bounds`)
- Test: `tests/test_api_labels.py` (append)

**Interfaces:**
- Consumes: `record_boundary_correction` (Task 1), `_rally_det_span` (Task 2).
- Produces: no new symbols. `POST /api/rallies/{rally_id}/bounds` now also appends a boundary-bearing label, and 404s on an unknown rally where it previously returned 200 silently.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_labels.py`:

```python
def test_a_boundary_drag_records_a_signed_correction(client, conn, seeded):
    rally = _first_rally(conn)
    client.post(f"/api/rallies/{rally['id']}/bounds",
                json={"start_ms": 1400, "end_ms": 4600})

    rows = client.get(f"/api/sources/{seeded['source_id']}/labels").json()
    assert len(rows) == 1
    assert rows[0]["verdict"] is None
    assert rows[0]["span_start_ms"] == 1000
    assert rows[0]["span_end_ms"] == 5000
    assert rows[0]["true_start_ms"] == 1400
    assert rows[0]["true_end_ms"] == 4600
    assert rows[0]["boundary_flags"] == ["start_early", "end_late"]


def test_a_drag_back_to_the_detector_span_records_nothing(client, conn, seeded):
    rally = _first_rally(conn)
    client.post(f"/api/rallies/{rally['id']}/bounds",
                json={"start_ms": 1000, "end_ms": 5000})
    assert client.get(f"/api/sources/{seeded['source_id']}/labels").json() == []


def test_bounds_404s_on_an_unknown_rally(client, seeded):
    r = client.post("/api/rallies/nope/bounds", json={"start_ms": 1, "end_ms": 2})
    assert r.status_code == 404


def test_a_boundary_drag_does_not_overwrite_an_existing_verdict(client, conn, seeded):
    # Two rows for one span: the verdict from label mode and the correction
    # from the drag. latest_labels returns the drag (it is later), and the
    # verdict row is still in the table for the exporter to find.
    rally = _first_rally(conn)
    client.post(f"/api/rallies/{rally['id']}/label",
                json={"verdict": "clean", "boundary_flags": []})
    client.post(f"/api/rallies/{rally['id']}/bounds",
                json={"start_ms": 1400, "end_ms": 4600})

    total = conn.execute("SELECT COUNT(*) FROM rally_labels").fetchone()[0]
    assert total == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_api_labels.py -q -k boundary or bounds`
Expected: FAIL — `test_a_boundary_drag_records_a_signed_correction` gets `[]`, `test_bounds_404s_on_an_unknown_rally` gets 200.

- [ ] **Step 3: Add the import**

In `bootleg/api/routes.py`, extend the Task 2 import line to:

```python
from bootleg.db.labels import (
    FLAG_ORDER,
    VERDICTS,
    add_label,
    latest_labels,
    parse_flags,
    record_boundary_correction,
)
```

- [ ] **Step 4: Rewrite `api_bounds`**

Replace lines 169-173 of `bootleg/api/routes.py`:

```python
@router.post("/api/rallies/{rally_id}/bounds")
def api_bounds(rally_id: str, body: BoundsBody, request: Request):
    conn = _conn(request)
    # Resolved before the write, both to 404 on a rally a re-segment in
    # another tab already deleted (set_bounds alone would silently update
    # nothing and report success) and because det_* is what the label
    # anchors to.
    span = _rally_det_span(conn, rally_id)
    # Label before bounds, not after: set_bounds and record_boundary_correction
    # each commit independently, so whichever runs second is the one a crash
    # between the two can lose. Losing the bounds write just means the drag
    # didn't visibly save and the reviewer retries. Losing the label after
    # the bounds already landed is worse and silent -- the UI reports success
    # while the correction that was the whole point never reaches the corpus.
    # Ordering the label first turns that failure mode into a loud one: the
    # request fails and the reviewer retries, and a retried label is harmless
    # since it only reads immutable det_* (captured above) and appends a row
    # that `latest_labels` will supersede if needed.
    # Every drag is ground truth: det_start_ms sits immutable beside the
    # edited start_ms, so the difference is a signed detector error in
    # milliseconds. It used to be destroyed by the next replace_rallies;
    # recording it here is the cheaper half of the whole corpus, and costs
    # the reviewer no extra keystrokes.
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

- [ ] **Step 5: Run the tests to verify they pass**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_api_labels.py -q`
Expected: PASS, 12 passed

- [ ] **Step 6: Run the full suite and the linter**

Run: `~/miniconda3/envs/bootleg/bin/pytest -q && ~/miniconda3/envs/bootleg/bin/ruff check bootleg tests`
Expected: all pass, ruff clean. `tests/test_api.py::test_bounds_endpoint_does_not_touch_det_columns` and `::test_bounds_rejects_inverted_range` must still pass unchanged.

- [ ] **Step 7: Commit**

```bash
git add bootleg/api/routes.py tests/test_api_labels.py
git commit -m "feat(api): every boundary drag records a signed correction

det_start_ms has always sat immutable beside the edited start_ms, so the
difference is a detector error in milliseconds -- produced by ordinary
review and deleted by the next replace_rallies. Now it lands in the label
corpus instead. Also 404s on an unknown rally, which set_bounds alone
reported as success while updating nothing."
```

---

### Task 4: The scorer

**Files:**
- Create: `bootleg/label_score.py`
- Modify: `bootleg/db/rallies.py:14` (rename `_overlap_fraction` → `overlap_fraction`, update its two call sites)
- Test: `tests/test_label_score.py`

**Interfaces:**
- Consumes: `bootleg.detect.segment.Interval`, `bootleg.db.rallies.overlap_fraction`, `bootleg.db.labels.parse_flags`.
- Produces:
  - `MATCH_OVERLAP_MIN: float`
  - `@dataclass(frozen=True) LabelRow(span_start_ms, span_end_ms, verdict, true_start_ms, true_end_ms)`
  - `rows_to_labels(rows: Iterable[sqlite3.Row]) -> list[LabelRow]`
  - `@dataclass(frozen=True) LabelScore` with fields `matched_play, matched_not_play, unknown, missed_clean, labelled_clean, start_bias_ms, end_bias_ms, start_mae_ms, end_mae_ms, boundary_n` and properties `precision`, `span_recall`
  - `score_against_labels(intervals: list[Interval], labels: list[LabelRow]) -> LabelScore`

Top-level module, not `bootleg/detect/`: `detect/` never imports `bootleg.db` and that layering is worth keeping. `bootleg/setup.py` is the existing precedent for a top-level module that spans layers.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_label_score.py`:

```python
"""Scorer arithmetic.

Synthetic inputs are correct here and do not violate the repo's rule against
synthetic fixtures: that rule bans *calibrating tuning constants* against
hand-written data, and this file fits no constant. It checks that overlap
matching, medians and the unknown count are computed the way the spec says.
"""

from bootleg.detect.segment import Interval
from bootleg.label_score import LabelRow, score_against_labels


def label(start, end, verdict="clean", true_start=None, true_end=None):
    return LabelRow(span_start_ms=start, span_end_ms=end, verdict=verdict,
                    true_start_ms=true_start, true_end_ms=true_end)


def test_a_candidate_over_a_clean_label_counts_as_precision_hit():
    s = score_against_labels([Interval(1000, 5000, 0.8)], [label(1000, 5000)])
    assert (s.matched_play, s.matched_not_play, s.unknown) == (1, 0, 0)
    assert s.precision == 1.0


def test_a_candidate_over_a_not_play_label_is_a_false_positive():
    s = score_against_labels([Interval(1000, 5000, 0.8)],
                             [label(1000, 5000, verdict="not_play")])
    assert (s.matched_play, s.matched_not_play) == (0, 1)
    assert s.precision == 0.0


def test_partly_counts_as_play():
    s = score_against_labels([Interval(1000, 5000, 0.8)],
                             [label(1000, 5000, verdict="partly")])
    assert s.matched_play == 1


def test_unsure_is_excluded_from_precision_entirely():
    # Not a hit and not a miss. Forcing an undecidable clip into either
    # column would inject noise while looking like data.
    s = score_against_labels([Interval(1000, 5000, 0.8)],
                             [label(1000, 5000, verdict="unsure")])
    assert (s.matched_play, s.matched_not_play, s.unknown) == (0, 0, 0)
    assert s.precision is None


def test_a_candidate_matching_no_label_is_unknown_not_a_false_positive():
    s = score_against_labels([Interval(60000, 65000, 0.8)], [label(1000, 5000)])
    assert s.unknown == 1
    assert (s.matched_play, s.matched_not_play) == (0, 0)
    assert s.precision is None


def test_overlap_below_the_floor_does_not_match():
    # 500 ms of overlap against a 4000 ms candidate is 12.5%.
    s = score_against_labels([Interval(4500, 8500, 0.8)], [label(1000, 5000)])
    assert s.unknown == 1


def test_a_candidate_takes_its_best_overlapping_label_when_several_qualify():
    # 0.80 overlap against 1.00 -- both clear the floor, and the closer one wins.
    s = score_against_labels(
        [Interval(1000, 5000, 0.8)],
        [label(500, 3000, verdict="not_play"), label(1000, 5000, verdict="clean")],
    )
    assert s.matched_play == 1
    assert s.matched_not_play == 0


def test_a_clean_label_with_no_candidate_is_a_miss():
    s = score_against_labels([], [label(1000, 5000), label(9000, 14000)])
    assert (s.labelled_clean, s.missed_clean) == (2, 2)
    assert s.span_recall == 0.0


def test_span_recall_is_none_when_nothing_clean_is_labelled():
    s = score_against_labels([Interval(1000, 5000, 0.8)],
                             [label(1000, 5000, verdict="not_play")])
    assert s.span_recall is None


def test_boundary_bias_is_signed_and_positive_means_the_candidate_opens_late():
    # Truth starts at 1400, candidate at 1000 -> the candidate opens 400 ms
    # early, i.e. bias -400.
    s = score_against_labels(
        [Interval(1000, 5000, 0.8)],
        [label(1000, 5000, verdict=None, true_start=1400, true_end=4600)],
    )
    assert s.start_bias_ms == -400
    assert s.end_bias_ms == 400
    assert s.boundary_n == 1


def test_boundary_mae_is_absolute_and_survives_cancelling_signs():
    s = score_against_labels(
        [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7)],
        [
            label(1000, 5000, verdict=None, true_start=1400, true_end=5000),
            label(9000, 14000, verdict=None, true_start=8600, true_end=14000),
        ],
    )
    # -400 and +400 cancel in the bias, but not in the MAE. Reporting only
    # bias would show a detector with 400 ms of scatter as perfect.
    assert s.start_bias_ms == 0
    assert s.start_mae_ms == 400


def test_boundary_stats_are_none_when_no_label_carries_a_corrected_span():
    s = score_against_labels([Interval(1000, 5000, 0.8)], [label(1000, 5000)])
    assert s.start_bias_ms is None
    assert s.start_mae_ms is None
    assert s.boundary_n == 0


def test_a_verdict_less_boundary_row_still_contributes_boundary_stats_only():
    s = score_against_labels(
        [Interval(1000, 5000, 0.8)],
        [label(1000, 5000, verdict=None, true_start=1400, true_end=4600)],
    )
    assert (s.matched_play, s.matched_not_play, s.unknown) == (0, 0, 0)
    assert s.boundary_n == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_label_score.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'bootleg.label_score'`

- [ ] **Step 3: Make the overlap rule public**

In `bootleg/db/rallies.py`, rename `_overlap_fraction` to `overlap_fraction` (line 14) and update its two references inside `_overlaps_any` (line 24) — nothing outside the module used it. Add to its definition:

```python
def overlap_fraction(a_start: int, a_end: int, b_start: int, b_end: int) -> float:
    """Overlap as a fraction of the *shorter* of the two spans.

    Public because the label scorer matches candidate intervals to labelled
    spans by the same rule replace_rallies uses to carry stars across a
    re-segment. One definition, so a rally that would inherit a star and a
    candidate that would count against a label can never disagree about what
    "the same rally" means.
    """
```

- [ ] **Step 4: Write `bootleg/label_score.py`**

```python
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median

from bootleg.db.rallies import STAR_OVERLAP_MIN, overlap_fraction
from bootleg.detect.segment import Interval

# The same floor replace_rallies uses to carry a star across a re-segment.
# Aliased rather than re-declared so the two can never drift apart.
MATCH_OVERLAP_MIN = STAR_OVERLAP_MIN

# Verdicts that assert play happened. 'unsure' is deliberately absent: two of
# six clips inspected during validation were recorded as "walking, racket
# down, no ball visible -- stills cannot settle it", and forcing those into
# either column would inject noise while looking like data.
PLAY_VERDICTS = ("clean", "partly")


@dataclass(frozen=True)
class LabelRow:
    """One human judgement, decoupled from sqlite so the scorer is pure."""

    span_start_ms: int
    span_end_ms: int
    verdict: str | None
    true_start_ms: int | None
    true_end_ms: int | None


def rows_to_labels(rows: Iterable[sqlite3.Row]) -> list[LabelRow]:
    return [
        LabelRow(
            span_start_ms=r["span_start_ms"],
            span_end_ms=r["span_end_ms"],
            verdict=r["verdict"],
            true_start_ms=r["true_start_ms"],
            true_end_ms=r["true_end_ms"],
        )
        for r in rows
    ]


@dataclass(frozen=True)
class LabelScore:
    matched_play: int
    matched_not_play: int
    unknown: int
    missed_clean: int
    labelled_clean: int
    boundary_n: int
    start_bias_ms: float | None
    end_bias_ms: float | None
    start_mae_ms: float | None
    end_mae_ms: float | None

    @property
    def precision(self) -> float | None:
        decided = self.matched_play + self.matched_not_play
        return None if decided == 0 else self.matched_play / decided

    @property
    def span_recall(self) -> float | None:
        """Recall over labelled spans ONLY.

        This can never see play the detector did not propose, because every
        label in the corpus attaches to a span it did. The validation set was
        built the other way on purpose -- six of its fifteen windows are spans
        the detector ignored, two of which contain play. Anything rendering
        this number must name it "span recall (labelled spans only)"; letting
        a metric imply coverage it does not have is the error that cost the
        last round.
        """
        if self.labelled_clean == 0:
            return None
        return (self.labelled_clean - self.missed_clean) / self.labelled_clean


def _best_match(iv: Interval, labels: list[LabelRow]) -> int | None:
    """Index of the labelled span this candidate best corresponds to, or None.

    Returns an index rather than the row itself so the caller can record which
    labels were hit. LabelRow is a frozen dataclass and compares by value, so
    two identical rows would be indistinguishable by identity or equality --
    position is the only stable handle.
    """
    best: int | None = None
    best_frac = 0.0
    for i, lab in enumerate(labels):
        frac = overlap_fraction(iv.start_ms, iv.end_ms, lab.span_start_ms, lab.span_end_ms)
        if frac >= MATCH_OVERLAP_MIN and frac > best_frac:
            best, best_frac = i, frac
    return best


def score_against_labels(intervals: list[Interval], labels: list[LabelRow]) -> LabelScore:
    """Score one candidate segmentation against the human corpus.

    Matching is by >50% overlap, not exact equality: a different threshold
    moves every edge, so an exact match would report zero on a segmentation
    that is obviously the same set of rallies. (The read path in the API is
    the opposite -- it matches exactly, because "have I judged this exact
    detector output before" has no approximate answer.)
    """
    matched_play = matched_not_play = unknown = 0
    start_errs: list[int] = []
    end_errs: list[int] = []
    hit: set[int] = set()

    for iv in intervals:
        i = _best_match(iv, labels)
        if i is None:
            unknown += 1
            continue
        hit.add(i)
        lab = labels[i]
        if lab.verdict in PLAY_VERDICTS:
            matched_play += 1
        elif lab.verdict == "not_play":
            matched_not_play += 1
        # 'unsure' and a verdict-less boundary row fall through: neither is a
        # precision hit nor a miss, but both may still carry a corrected span.
        if lab.true_start_ms is not None and lab.true_end_ms is not None:
            # Signed, and positive means the candidate opens/closes AFTER the
            # human's edge.
            start_errs.append(iv.start_ms - lab.true_start_ms)
            end_errs.append(iv.end_ms - lab.true_end_ms)

    clean = [i for i, lab in enumerate(labels) if lab.verdict == "clean"]
    missed_clean = sum(1 for i in clean if i not in hit)

    return LabelScore(
        matched_play=matched_play,
        matched_not_play=matched_not_play,
        unknown=unknown,
        missed_clean=missed_clean,
        labelled_clean=len(clean),
        boundary_n=len(start_errs),
        start_bias_ms=median(start_errs) if start_errs else None,
        end_bias_ms=median(end_errs) if end_errs else None,
        # Reported alongside the bias, never instead of it: equal and opposite
        # errors cancel in a median, so a detector with 400 ms of scatter in
        # both directions reads as perfect on bias alone.
        start_mae_ms=median(abs(e) for e in start_errs) if start_errs else None,
        end_mae_ms=median(abs(e) for e in end_errs) if end_errs else None,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_label_score.py -q`
Expected: PASS, 13 passed

- [ ] **Step 6: Run the full suite and the linter**

Run: `~/miniconda3/envs/bootleg/bin/pytest -q && ~/miniconda3/envs/bootleg/bin/ruff check bootleg tests`
Expected: all pass. `tests/test_db.py` and `tests/test_api.py` exercise `replace_rallies`' star carry-over and must still pass after the rename.

- [ ] **Step 7: Commit**

```bash
git add bootleg/label_score.py bootleg/db/rallies.py tests/test_label_score.py
git commit -m "feat: score a candidate segmentation against the label corpus

Matching is by >50% overlap, reusing replace_rallies' own rule rather than
reinventing it -- a new threshold moves every edge, so exact matching would
report zero on an obviously-equivalent segmentation.

span_recall is named for what it is: it cannot see play the detector never
proposed. 'unsure' is excluded from precision in both directions, and MAE
is reported beside bias because equal and opposite errors cancel in a
median."
```

---

### Task 5: `bootleg labels export` and `bootleg labels score`

**Files:**
- Modify: `bootleg/cli.py` (imports 8-25, new `cmd_labels_export`/`cmd_labels_score` after `cmd_segment` which ends near line 237, parser wiring near line 341)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `latest_labels`, `parse_flags` (Task 1), `rows_to_labels`, `score_against_labels` (Task 4), `read_features`, `params_for_frames`, `segment`.
- Produces: `cmd_labels_export(args) -> int`, `cmd_labels_score(args) -> int`, and the `labels` subparser with `export` and `score`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_labels_export_writes_the_corpus_as_json(library, conn, capsys, tmp_path):
    # json, main, add_source, find_or_create_session_for_date and
    # write_features are already imported at the top of this file.
    from bootleg.db.labels import add_label
    from bootleg.db.rallies import replace_rallies
    from bootleg.detect.segment import Interval

    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=1920, height=1080, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id, [Interval(1000, 5000, 0.8)])
    add_label(conn, source_id=source_id, span_start_ms=1000, span_end_ms=5000,
              verdict="clean", boundary_flags=["end_late"])
    conn.close()

    out = tmp_path / "labels.json"
    rc = main(["--library", str(library.root), "labels", "export", source_id,
               "--out", str(out)])
    assert rc == 0

    payload = json.loads(out.read_text())
    assert payload["source_id"] == source_id
    assert payload["source"] == "sessions/2026-08-18/sources/01"
    assert payload["labels"] == [{
        "span_start_ms": 1000, "span_end_ms": 5000, "verdict": "clean",
        "boundary_flags": ["end_late"], "true_start_ms": None, "true_end_ms": None,
    }]


def test_labels_export_on_an_unknown_source_fails(library, conn, capsys):
    conn.close()
    rc = main(["--library", str(library.root), "labels", "export", "nope"])
    assert rc == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_labels_score_reports_the_recall_caveat_and_the_unknown_count(
    library, conn, capsys, ground_features
):
    """The two output constraints the spec makes non-negotiable.

    A bare "recall" would repeat the error that cost the last validation
    round, and a precision figure with the unknown count hidden conceals a
    sweep that matched three candidates and missed forty.
    """
    from bootleg.db.labels import add_label

    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=1920, height=1080, fps=30.0, original_name="IMG_9000.MOV",
    )
    add_label(conn, source_id=source_id, span_start_ms=1000, span_end_ms=5000,
              verdict="not_play")
    conn.close()

    src_dir = library.source_dir(session_id, idx)
    src_dir.mkdir(parents=True, exist_ok=True)
    write_features(src_dir / "features.jsonl", ground_features)

    rc = main(["--library", str(library.root), "labels", "score", source_id])
    assert rc == 0
    out = capsys.readouterr().out
    assert "span recall (labelled spans only)" in out
    assert "cannot see play the detector never proposed" in out
    assert "unknown" in out


def test_labels_score_without_features_fails(library, conn, capsys):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=1920, height=1080, fps=30.0, original_name="IMG_9000.MOV",
    )
    conn.close()

    rc = main(["--library", str(library.root), "labels", "score", source_id])
    assert rc == 1
    assert "not been detected" in capsys.readouterr().err.lower()
```

`tests/test_cli.py` already imports `json`, `main`, `add_source`, `find_or_create_session_for_date` and `write_features` at module level — do not re-import them inside the test bodies.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_cli.py -q -k labels`
Expected: FAIL — `argparse` exits with "invalid choice: 'labels'"

- [ ] **Step 3: Add the imports**

In `bootleg/cli.py`, add after the `bootleg.db.presets` import (line 10):

```python
from bootleg.db.labels import latest_labels, parse_flags
```

and after the `bootleg.jobs.worker` import (line 22):

```python
from bootleg.label_score import rows_to_labels, score_against_labels
```

- [ ] **Step 4: Write the two commands**

In `bootleg/cli.py`, add after `cmd_segment`:

```python
def _source_or_fail(conn, source_id: str):
    source = get_source(conn, source_id)
    if source is None:
        print(f"source not found: {source_id}", file=sys.stderr)
        return None
    return source


def cmd_labels_export(args) -> int:
    library = _library(args)
    conn = connect(library.db_path)
    migrate(conn)
    source = _source_or_fail(conn, args.source_id)
    if source is None:
        return 1

    rows = latest_labels(conn, args.source_id)
    payload = {
        # Library-relative, matching labels_2026-08-18_source01.json's own
        # "source" field -- an absolute path would be meaningless once the
        # export is committed and read on another machine.
        "source": str(
            library.source_dir(source["session_id"], source["idx"]).relative_to(library.root)
        ),
        "source_id": args.source_id,
        "exported_on": datetime.now(UTC).date().isoformat(),
        "labels": [
            {
                "span_start_ms": r["span_start_ms"],
                "span_end_ms": r["span_end_ms"],
                "verdict": r["verdict"],
                "boundary_flags": parse_flags(r["boundary_flags"]),
                "true_start_ms": r["true_start_ms"],
                "true_end_ms": r["true_end_ms"],
            }
            for r in rows
        ],
    }
    text = json.dumps(payload, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n")
        print(f"wrote {len(payload['labels'])} labels to {args.out}")
    else:
        print(text)
    return 0


def _fmt_ms(v: float | None) -> str:
    return "--" if v is None else f"{v:+.0f} ms"


def _fmt_pct(v: float | None) -> str:
    return "--" if v is None else f"{v * 100:.0f}%"


def cmd_labels_score(args) -> int:
    library = _library(args)
    conn = connect(library.db_path)
    migrate(conn)
    source = _source_or_fail(conn, args.source_id)
    if source is None:
        return 1

    path = library.source_dir(source["session_id"], source["idx"]) / "features.jsonl"
    if not path.exists():
        print(f"source has not been detected yet: {args.source_id}", file=sys.stderr)
        return 1

    frames = read_features(path)
    params = params_for_frames(frames, threshold=args.threshold)
    intervals = segment(frames, params)
    labels = rows_to_labels(latest_labels(conn, args.source_id))
    score = score_against_labels(intervals, labels)

    print(f"{len(intervals)} candidate rallies at threshold {params.threshold:.2f}"
          f" against {len(labels)} labelled spans")
    print(f"  precision                        {_fmt_pct(score.precision)}"
          f"  ({score.matched_play} play / {score.matched_play + score.matched_not_play} decided)")
    print(f"  span recall (labelled spans only) {_fmt_pct(score.span_recall)}"
          f"  ({score.labelled_clean - score.missed_clean} of {score.labelled_clean} clean)")
    print(f"  unknown                          {score.unknown}"
          "  (candidates matching no label)")
    print(f"  start bias / MAE                 {_fmt_ms(score.start_bias_ms)}"
          f" / {_fmt_ms(score.start_mae_ms)}   (n={score.boundary_n})")
    print(f"  end bias / MAE                   {_fmt_ms(score.end_bias_ms)}"
          f" / {_fmt_ms(score.end_mae_ms)}")
    # Printed every run, not as a footnote in the docs. Every label in the
    # corpus attaches to a span the detector proposed, so this figure cannot
    # see play the detector never proposed -- and a number called plain
    # "recall" here would repeat exactly the mistake that let audio-impact
    # clustering stand in as ground truth.
    print("\n  span recall cannot see play the detector never proposed:"
          " every label sits on a span it did.")
    return 0
```

- [ ] **Step 5: Wire the parser**

In `bootleg/cli.py`, add after the `source` subparser block (near line 347):

```python
    p = sub.add_parser("labels", help="export or score the human label corpus")
    labels_sub = p.add_subparsers(dest="labels_cmd", required=True)

    le = labels_sub.add_parser("export", help="write a source's labels as JSON")
    le.add_argument("source_id")
    le.add_argument("--out", help="write to this path instead of stdout")
    le.set_defaults(func=cmd_labels_export)

    ls = labels_sub.add_parser("score", help="score a segmentation against the labels")
    ls.add_argument("source_id")
    ls.add_argument("--threshold", type=float, default=None,
                    help="override the profile's default score threshold")
    ls.set_defaults(func=cmd_labels_score)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `~/miniconda3/envs/bootleg/bin/pytest tests/test_cli.py -q -k labels`
Expected: PASS, 4 passed

- [ ] **Step 7: Run the full suite and the linter**

Run: `~/miniconda3/envs/bootleg/bin/pytest -q && ~/miniconda3/envs/bootleg/bin/ruff check bootleg tests`
Expected: all pass, ruff clean

- [ ] **Step 8: Commit**

```bash
git add bootleg/cli.py tests/test_cli.py
git commit -m "feat(cli): bootleg labels export and bootleg labels score

score re-runs segment() over cached features -- pure and ~200 ms -- so a
threshold sweep against fixed human judgement is a sub-second operation
with no GPU involved. That is the property the two-stage detector split was
built for.

The recall figure is printed as 'span recall (labelled spans only)' with a
line stating what it cannot see, and the unknown count is always shown: a
sweep at 100% precision over three matched candidates and forty unknowns is
not a good sweep."
```

---

### Task 6: The label-mode state machine

**Files:**
- Create: `web/src/lib/labels.ts`
- Modify: `web/src/lib/types.ts` (append `LabelRecord`)
- Test: `web/tests/labels.test.ts`

**Interfaces:**
- Consumes: `Rally` from `./types`, `UndoStack` from `./undo`.
- Produces:
  - `type Verdict = 'clean' | 'not_play' | 'partly' | 'unsure'`
  - `type BoundaryFlag = 'start_early' | 'start_late' | 'end_early' | 'end_late'`
  - `const FLAG_ORDER: readonly BoundaryFlag[]`
  - `interface LabelRecord { span_start_ms, span_end_ms, verdict: Verdict | null, boundary_flags: BoundaryFlag[], true_start_ms: number | null, true_end_ms: number | null }` (exported from `types.ts`)
  - `interface LabelAction { rallyId, verdict: Verdict, flags: BoundaryFlag[], previousVerdict: Verdict | null, previousFlags: BoundaryFlag[] }`
  - `class LabelController` with `current`, `index`, `total`, `labelledCount`, `currentVerdict`, `currentFlags`, `flagsEnabled`, `setVerdict(v)`, `toggleFlag(f)`, `next()`, `back()`, `undo()`, `revert(action)`

- [ ] **Step 1: Add the `LabelRecord` type**

Append to `web/src/lib/types.ts`:

```ts
export interface LabelRecord {
  span_start_ms: number
  span_end_ms: number
  verdict: 'clean' | 'not_play' | 'partly' | 'unsure' | null
  boundary_flags: ('start_early' | 'start_late' | 'end_early' | 'end_late')[]
  true_start_ms: number | null
  true_end_ms: number | null
}
```

- [ ] **Step 2: Write the failing tests**

Create `web/tests/labels.test.ts`:

```ts
import { beforeEach, describe, expect, it } from 'vitest'
import { LabelController } from '../src/lib/labels'
import type { LabelRecord, Rally } from '../src/lib/types'

function rally(idx: number, over: Partial<Rally> = {}): Rally {
  return {
    id: `r${idx}`,
    session_id: 's',
    source_id: 'src',
    idx,
    start_ms: idx * 10000,
    end_ms: idx * 10000 + 8000,
    det_start_ms: idx * 10000,
    det_end_ms: idx * 10000 + 8000,
    confidence: 0.7,
    starred: 0,
    rejected: 0,
    reviewed_at: null,
    ...over,
  }
}

function record(over: Partial<LabelRecord> = {}): LabelRecord {
  return {
    span_start_ms: 10000,
    span_end_ms: 18000,
    verdict: 'clean',
    boundary_flags: [],
    true_start_ms: null,
    true_end_ms: null,
    ...over,
  }
}

describe('LabelController', () => {
  let c: LabelController

  beforeEach(() => {
    c = new LabelController([rally(1), rally(2), rally(3)], [])
  })

  it('starts on the first rally', () => {
    expect(c.current?.id).toBe('r1')
    expect(c.index).toBe(0)
    expect(c.total).toBe(3)
  })

  it('includes rejected rallies, unlike the review queue', () => {
    // A rejected rally is exactly the not_play the corpus most needs. The
    // queue filters them out because it is a review flow; this is not.
    const withRejected = new LabelController([rally(1, { rejected: 1 }), rally(2)], [])
    expect(withRejected.total).toBe(2)
    expect(withRejected.current?.id).toBe('r1')
  })

  it('setVerdict returns an action carrying the previous state', () => {
    const action = c.setVerdict('clean')
    expect(action).toEqual({
      rallyId: 'r1',
      verdict: 'clean',
      flags: [],
      previousVerdict: null,
      previousFlags: [],
    })
    expect(c.currentVerdict).toBe('clean')
  })

  it('setVerdict does not advance', () => {
    // Flags are added to the same span after the verdict, so the cursor has
    // to stay put -- and 95218d3 removed auto-advance from the queue for the
    // same reason.
    c.setVerdict('clean')
    expect(c.index).toBe(0)
  })

  it('toggleFlag adds then removes, and keeps canonical order', () => {
    c.setVerdict('clean')
    c.toggleFlag('end_late')
    c.toggleFlag('start_early')
    expect(c.currentFlags).toEqual(['start_early', 'end_late'])
    c.toggleFlag('end_late')
    expect(c.currentFlags).toEqual(['start_early'])
  })

  it('flags are disabled until a verdict is set', () => {
    expect(c.flagsEnabled).toBe(false)
    expect(c.toggleFlag('end_late')).toBeNull()
    expect(c.currentFlags).toEqual([])
  })

  it('flags are disabled under not_play and unsure', () => {
    // No boundary to be wrong about on a span holding no rally.
    c.setVerdict('not_play')
    expect(c.flagsEnabled).toBe(false)
    expect(c.toggleFlag('end_late')).toBeNull()

    c.setVerdict('unsure')
    expect(c.flagsEnabled).toBe(false)
  })

  it('changing a verdict to not_play clears flags already set', () => {
    c.setVerdict('clean')
    c.toggleFlag('end_late')
    const action = c.setVerdict('not_play')
    expect(c.currentFlags).toEqual([])
    expect(action?.flags).toEqual([])
  })

  it('flags are enabled under partly', () => {
    c.setVerdict('partly')
    expect(c.flagsEnabled).toBe(true)
  })

  it('seeds from existing labels matched on the exact detector span', () => {
    const seeded = new LabelController(
      [rally(1), rally(2)],
      [record({ span_start_ms: 10000, span_end_ms: 18000, verdict: 'not_play' })],
    )
    expect(seeded.currentVerdict).toBe('not_play')
    seeded.next()
    expect(seeded.currentVerdict).toBeNull()
  })

  it('does not seed from a label whose span merely overlaps', () => {
    // A re-segment that moved this edge produced different detector output,
    // so the old judgement is not a judgement of this span. Matching by
    // overlap here would silently attribute a verdict to a clip nobody
    // watched.
    const seeded = new LabelController(
      [rally(1)],
      [record({ span_start_ms: 10200, span_end_ms: 18000, verdict: 'not_play' })],
    )
    expect(seeded.currentVerdict).toBeNull()
  })

  it('ignores a verdict-less boundary row when seeding', () => {
    // It carries a corrected span, not a judgement -- rendering it as a
    // verdict would invent one.
    const seeded = new LabelController(
      [rally(1)],
      [record({ verdict: null, boundary_flags: ['end_late'], true_end_ms: 17000 })],
    )
    expect(seeded.currentVerdict).toBeNull()
    expect(seeded.currentFlags).toEqual([])
  })

  it('labelledCount counts rallies with a verdict, seeded or set', () => {
    const seeded = new LabelController(
      [rally(1), rally(2), rally(3)],
      [record({ span_start_ms: 10000, span_end_ms: 18000 })],
    )
    expect(seeded.labelledCount).toBe(1)
    seeded.next()
    seeded.setVerdict('not_play')
    expect(seeded.labelledCount).toBe(2)
  })

  it('next and back move the cursor and clamp at both ends', () => {
    c.back()
    expect(c.index).toBe(0)
    c.next()
    c.next()
    c.next()
    c.next()
    expect(c.index).toBe(2)
  })

  it('undo restores the cursor and clears a first-time verdict', () => {
    c.setVerdict('clean')
    c.next()
    c.setVerdict('not_play')

    // Returns null: the restored state has no verdict, and the corpus is
    // append-only with no "unlabel" route, so there is nothing to persist.
    expect(c.undo()).toBeNull()
    expect(c.index).toBe(1)
    expect(c.currentVerdict).toBeNull()
  })

  it('undo of a changed verdict returns the restored verdict to persist', () => {
    c.setVerdict('clean')
    c.setVerdict('not_play')

    const action = c.undo()
    expect(action?.rallyId).toBe('r1')
    expect(action?.verdict).toBe('clean')
    expect(c.currentVerdict).toBe('clean')
  })

  it('undo returns null with nothing to undo', () => {
    expect(c.undo()).toBeNull()
  })

  it('revert restores exactly the failed action rallys state, wherever the cursor is', () => {
    // Mirrors QueueController.revert: a failed POST must not move the user's
    // position or consume their undo.
    const action = c.setVerdict('clean')!
    c.next()
    c.revert(action)
    expect(c.index).toBe(1)
    c.back()
    expect(c.currentVerdict).toBeNull()
  })
})
```

- [ ] **Step 3: Run the tests to verify they fail**

Run (from `web/`): `npx vitest run tests/labels.test.ts`
Expected: FAIL — cannot resolve `../src/lib/labels`

- [ ] **Step 4: Write `web/src/lib/labels.ts`**

```ts
import { UndoStack } from './undo'
import type { LabelRecord, Rally } from './types'

export type Verdict = 'clean' | 'not_play' | 'partly' | 'unsure'
export type BoundaryFlag = 'start_early' | 'start_late' | 'end_early' | 'end_late'

// Mirrors FLAG_ORDER in bootleg/db/labels.py. The server re-orders on write
// anyway, but sending and rendering the same order keeps the UI's flag row
// from reshuffling as you toggle.
export const FLAG_ORDER: readonly BoundaryFlag[] = [
  'start_early',
  'start_late',
  'end_early',
  'end_late',
]

// Verdicts that admit a boundary error. A span holding no rally has no
// boundary to be wrong about, and 'unsure' means the clip could not be
// judged at all.
const FLAGGABLE: readonly Verdict[] = ['clean', 'partly']

export interface LabelAction {
  rallyId: string
  verdict: Verdict
  flags: BoundaryFlag[]
  previousVerdict: Verdict | null
  previousFlags: BoundaryFlag[]
}

interface HistoryEntry {
  index: number
  verdict: Verdict | null
  flags: BoundaryFlag[]
}

function spanKey(startMs: number, endMs: number): string {
  return `${startMs}:${endMs}`
}

/**
 * The label-mode state machine.
 *
 * Deliberately pure, for the same reason QueueController is: no DOM, no
 * fetch, no timers. jsdom has no <video>, so anything that lived in the
 * component would be untestable.
 */
export class LabelController {
  #rallies: Rally[]
  #verdicts = new Map<string, Verdict>()
  #flags = new Map<string, BoundaryFlag[]>()
  #index = 0
  #history = new UndoStack<HistoryEntry>()

  /**
   * `rallies` is NOT filtered by `rejected`, unlike QueueController. A
   * rejected rally is precisely the `not_play` the corpus is short of;
   * hiding it here would bias the set toward what the detector already gets
   * right.
   *
   * `existing` seeds from labels already stored. Matching is on the exact
   * detector span: a re-segment that moved an edge produced genuinely
   * different detector output, so the old judgement is not a judgement of
   * this span, and matching by overlap would attribute a verdict to a clip
   * nobody watched.
   */
  constructor(rallies: Rally[], existing: LabelRecord[]) {
    this.#rallies = rallies
    const bySpan = new Map<string, LabelRecord>()
    for (const rec of existing) bySpan.set(spanKey(rec.span_start_ms, rec.span_end_ms), rec)

    for (const r of rallies) {
      const rec = bySpan.get(spanKey(r.det_start_ms, r.det_end_ms))
      // A verdict-less row is a boundary correction from a drag, not a
      // judgement -- rendering it as one would invent a verdict the reviewer
      // never gave.
      if (!rec || rec.verdict === null) continue
      this.#verdicts.set(r.id, rec.verdict)
      this.#flags.set(r.id, [...rec.boundary_flags])
    }
  }

  get current(): Rally | undefined {
    return this.#rallies[this.#index]
  }

  get index(): number {
    return this.#index
  }

  get total(): number {
    return this.#rallies.length
  }

  get labelledCount(): number {
    return this.#verdicts.size
  }

  get currentVerdict(): Verdict | null {
    const r = this.current
    return r ? (this.#verdicts.get(r.id) ?? null) : null
  }

  get currentFlags(): BoundaryFlag[] {
    const r = this.current
    // Copied, not the live array: a caller holding this reference must not
    // be able to reach into controller state (e.g. `currentFlags.push(...)`)
    // without going through toggleFlag.
    return r ? [...(this.#flags.get(r.id) ?? [])] : []
  }

  get flagsEnabled(): boolean {
    const v = this.currentVerdict
    return v !== null && FLAGGABLE.includes(v)
  }

  #record(): void {
    const r = this.current
    if (!r) return
    this.#history.push({
      index: this.#index,
      verdict: this.#verdicts.get(r.id) ?? null,
      flags: [...(this.#flags.get(r.id) ?? [])],
    })
  }

  setVerdict(verdict: Verdict): LabelAction | null {
    const r = this.current
    if (!r) return null
    this.#record()
    const previousVerdict = this.#verdicts.get(r.id) ?? null
    const previousFlags = [...(this.#flags.get(r.id) ?? [])]

    this.#verdicts.set(r.id, verdict)
    // Dropping to a verdict that admits no boundary error clears whatever was
    // already flagged, so a not_play row can never carry an end_late that
    // contradicts it.
    // Copied rather than reused: `flags` becomes the array stored in
    // `#flags`, and `previousFlags` is handed back to the caller as the
    // action's pre-action snapshot. Aliasing the two would let a caller
    // mutating action.previousFlags corrupt controller state, and revert()
    // depends on previousFlags staying an immutable snapshot of what
    // preceded this action.
    const flags = FLAGGABLE.includes(verdict) ? [...previousFlags] : []
    this.#flags.set(r.id, flags)

    // Deliberately does not advance -- boundary flags are added to this same
    // span next, and `→` is the only thing that moves the cursor.
    return {
      rallyId: r.id,
      verdict,
      flags: [...flags],
      previousVerdict,
      previousFlags: [...previousFlags],
    }
  }

  toggleFlag(flag: BoundaryFlag): LabelAction | null {
    const r = this.current
    const verdict = this.currentVerdict
    if (!r || verdict === null || !FLAGGABLE.includes(verdict)) return null
    this.#record()
    const previousFlags = [...(this.#flags.get(r.id) ?? [])]
    const next = previousFlags.includes(flag)
      ? previousFlags.filter((f) => f !== flag)
      : [...previousFlags, flag]
    // Canonical order, so the rendered row does not reshuffle as you toggle.
    const ordered = FLAG_ORDER.filter((f) => next.includes(f))
    this.#flags.set(r.id, ordered)

    return {
      rallyId: r.id,
      verdict,
      flags: [...ordered],
      previousVerdict: verdict,
      previousFlags,
    }
  }

  next(): void {
    if (this.#index < this.#rallies.length - 1) this.#index += 1
  }

  back(): void {
    if (this.#index > 0) this.#index -= 1
  }

  undo(): LabelAction | null {
    const entry = this.#history.pop()
    if (!entry) return null
    this.#index = entry.index
    const r = this.#rallies[entry.index]
    if (!r) return null

    const previousVerdict = this.#verdicts.get(r.id) ?? null
    const previousFlags = [...(this.#flags.get(r.id) ?? [])]

    if (entry.verdict === null) this.#verdicts.delete(r.id)
    else this.#verdicts.set(r.id, entry.verdict)
    this.#flags.set(r.id, [...entry.flags])

    // Returns null when the restored state has no verdict: there is nothing
    // to POST, since the API has no "unlabel" and the corpus is append-only.
    // The caller simply has nothing to persist.
    if (entry.verdict === null) return null
    return {
      rallyId: r.id,
      verdict: entry.verdict,
      flags: [...entry.flags],
      previousVerdict,
      previousFlags,
    }
  }

  /**
   * Restores one rally's state after its POST failed.
   *
   * Action-correlated rather than position-correlated, exactly like
   * QueueController.revert: it targets `action.rallyId` wherever that sits,
   * never touches `#index`, and never pops `#history`. A failed network call
   * must not move the reviewer's position or consume their undo.
   */
  revert(action: LabelAction): void {
    if (action.previousVerdict === null) this.#verdicts.delete(action.rallyId)
    else this.#verdicts.set(action.rallyId, action.previousVerdict)
    this.#flags.set(action.rallyId, [...action.previousFlags])
  }
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run (from `web/`): `npx vitest run tests/labels.test.ts`
Expected: PASS, 18 passed

- [ ] **Step 6: Type-check and run the whole frontend suite**

Run (from `web/`): `npm run check && npx vitest run`
Expected: svelte-check clean, all vitest files pass

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/labels.ts web/src/lib/types.ts web/tests/labels.test.ts
git commit -m "feat(web): the label-mode state machine

Seeds from stored labels on the exact detector span, never by overlap: a
re-segment that moved an edge produced different detector output, so the
old judgement is not a judgement of this span.

Unlike QueueController it does not filter rejected rallies -- a rejected
rally is precisely the not_play the corpus is short of."
```

---

### Task 7: Label mode in the UI

**Files:**
- Create: `web/src/components/LabelMode.svelte`
- Modify: `web/src/lib/api.ts` (add `label`, `sourceLabels`)
- Modify: `web/src/components/QueueMode.svelte` (add `onopen_label` prop, `l` key, help line)
- Modify: `web/src/routes/Session.svelte` (third mode)
- Test: `web/tests/labels.test.ts` (append api-shape tests)

**Interfaces:**
- Consumes: `LabelController`, `FLAG_ORDER`, `Verdict`, `BoundaryFlag` (Task 6); `POST /api/rallies/{id}/label`, `GET /api/sources/{id}/labels` (Task 2).
- Produces: `api.label(id, verdict, boundary_flags)`, `api.sourceLabels(sourceId)`, and a `'label'` value for Session's `mode`.

- [ ] **Step 1: Add the API client methods**

In `web/src/lib/api.ts`, extend the type import on line 1:

```ts
import type { Job, LabelRecord, Preset, ScoreSeries, Session, SessionDetail, Source } from './types'
```

and add after the `setBounds` entry:

```ts
  label: (id: string, verdict: string, boundary_flags: string[]) =>
    post(`/api/rallies/${id}/label`, { verdict, boundary_flags }),
  sourceLabels: (sourceId: string) => req<LabelRecord[]>(`/api/sources/${sourceId}/labels`),
```

- [ ] **Step 2: Write the failing test for the persist shape**

Append to `web/tests/labels.test.ts`:

```ts
describe('persisting a label', () => {
  it('sends the verdict and flags for the action rally', async () => {
    const calls: Array<[string, string, string[]]> = []
    const fakeApi = {
      label: async (id: string, verdict: string, flags: string[]) => {
        calls.push([id, verdict, flags])
        return {}
      },
    }
    const c = new LabelController([rally(1)], [])
    const action = c.setVerdict('partly')!
    await persistLabel(action, fakeApi)
    expect(calls).toEqual([['r1', 'partly', []]])
  })

  it('reports a failure so the caller can revert', async () => {
    const fakeApi = {
      label: async () => {
        throw new Error('offline')
      },
    }
    const c = new LabelController([rally(1)], [])
    const action = c.setVerdict('clean')!
    expect(await persistLabel(action, fakeApi)).toEqual({ ok: false })
  })
})
```

Add `persistLabel` to the import at the top of the file:

```ts
import { LabelController, persistLabel } from '../src/lib/labels'
```

- [ ] **Step 3: Run the test to verify it fails**

Run (from `web/`): `npx vitest run tests/labels.test.ts -t persisting`
Expected: FAIL — `persistLabel` is not exported

- [ ] **Step 4: Add `persistLabel` to `web/src/lib/labels.ts`**

```ts
/** The subset of `api` that persisting a LabelAction needs. */
export interface LabelApi {
  label: (id: string, verdict: string, boundaryFlags: string[]) => Promise<unknown>
}

export type LabelOutcome = { ok: true } | { ok: false }

/**
 * Sends one LabelAction to the server.
 *
 * Every call appends a row -- the corpus is append-only by design, so
 * toggling a flag three times leaves three rows and `latest_labels` resolves
 * which is current. That is deliberate: a corrected judgement must never
 * erase the one it corrected.
 */
export async function persistLabel(
  action: LabelAction,
  api: LabelApi,
): Promise<LabelOutcome> {
  try {
    await api.label(action.rallyId, action.verdict, action.flags)
    return { ok: true }
  } catch {
    return { ok: false }
  }
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run (from `web/`): `npx vitest run tests/labels.test.ts`
Expected: PASS, 20 passed

- [ ] **Step 6: Write `web/src/components/LabelMode.svelte`**

```svelte
<script lang="ts">
  import { untrack } from 'svelte'
  import { api } from '../lib/api'
  import { isEditableTarget } from '../lib/keyboard'
  import { FLAG_ORDER, LabelController, persistLabel } from '../lib/labels'
  import { createToaster } from '../lib/toaster.svelte'
  import { formatDuration, formatTs } from '../lib/time'
  import type { BoundaryFlag, LabelAction, Verdict } from '../lib/labels'
  import type { LabelRecord, SessionDetail, Source } from '../lib/types'
  import VideoDeck from './VideoDeck.svelte'

  interface Props {
    detail: SessionDetail
    onclose: () => void
  }
  let { detail, onclose }: Props = $props()

  const VERDICT_KEYS: Record<string, Verdict> = {
    '1': 'clean',
    '2': 'not_play',
    '3': 'partly',
    '4': 'unsure',
  }
  // Left-hand keys are the clip's start and right-hand keys its end; the
  // first of each pair is early and the second late.
  const FLAG_KEYS: Record<string, BoundaryFlag> = {
    q: 'start_early',
    w: 'start_late',
    o: 'end_early',
    p: 'end_late',
  }
  const FLAG_LABELS: Record<BoundaryFlag, string> = {
    start_early: 'starts early (Q)',
    start_late: 'starts late (W)',
    end_early: 'ends early (O)',
    end_late: 'ends late (P)',
  }

  const toaster = createToaster()
  let controller = $state<LabelController | null>(null)
  let loadError = $state<string | null>(null)
  let version = $state(0)
  let deck = $state<VideoDeck>()

  // One fetch per source, merged. Labels are per-source and a session can
  // hold several; the controller matches them to rallies by exact detector
  // span, so a flat list is all it needs.
  $effect(() => {
    const sources = untrack(() => detail.sources)
    const rallies = untrack(() => detail.rallies)
    Promise.all(sources.map((s) => api.sourceLabels(s.id)))
      .then((lists) => {
        const flat: LabelRecord[] = lists.flat()
        controller = new LabelController(rallies, flat)
        version += 1
      })
      .catch((e) => (loadError = String(e)))
  })

  const current = $derived.by(() => {
    version
    return controller?.current
  })
  const verdict = $derived.by(() => {
    version
    return controller?.currentVerdict ?? null
  })
  const flags = $derived.by(() => {
    version
    return controller?.currentFlags ?? []
  })
  const flagsEnabled = $derived.by(() => {
    version
    return controller?.flagsEnabled ?? false
  })
  const stats = $derived.by(() => {
    version
    return {
      index: controller?.index ?? 0,
      total: controller?.total ?? 0,
      labelled: controller?.labelledCount ?? 0,
    }
  })

  function srcFor(sourceId: string): string {
    const s: Source | undefined = detail.sources.find((x) => x.id === sourceId)
    return s ? api.proxyUrl(detail.session.id, s.idx) : ''
  }

  async function apply(action: LabelAction | null): Promise<void> {
    version += 1
    if (!action) return
    const outcome = await persistLabel(action, api)
    if (!outcome.ok) {
      // Same reasoning as QueueMode: revert this action's rally without
      // moving the cursor or consuming the undo stack, surface it, and do
      // not halt the pass over a rare failure on a LAN box.
      controller?.revert(action)
      version += 1
      toaster.push("Couldn't save that label -- reverted")
    }
  }

  function onKey(e: KeyboardEvent) {
    if (isEditableTarget(e.target)) return
    if (e.metaKey || e.ctrlKey || e.altKey) return
    if (!controller) return

    const v = VERDICT_KEYS[e.key]
    if (v) {
      apply(controller.setVerdict(v))
      return
    }
    const f = FLAG_KEYS[e.key.toLowerCase()]
    if (f) {
      apply(controller.toggleFlag(f))
      return
    }
    switch (e.key) {
      case 'r':
      case 'R':
        deck?.replay()
        break
      case 'ArrowRight':
        e.preventDefault()
        controller.next()
        version += 1
        break
      case 'ArrowLeft':
        e.preventDefault()
        controller.back()
        version += 1
        break
      case 'u':
      case 'U':
        apply(controller.undo())
        break
      case ' ':
        e.preventDefault()
        if (deck?.paused()) deck?.play()
        else deck?.pause()
        break
      case 'l':
      case 'L':
      case 'Escape':
        onclose()
        break
    }
  }
</script>

<svelte:window onkeydown={onKey} />

{#if loadError}
  <p class="rounded bg-red-500/10 p-3 text-sm text-red-300">{loadError}</p>
{:else if !controller}
  <p class="text-sm text-neutral-400">Loading labels…</p>
{:else if !current}
  <section class="rounded-lg border border-neutral-800 p-8 text-center">
    <h2 class="text-lg font-semibold">Nothing to label</h2>
    <p class="mt-2 font-mono text-sm text-neutral-400">
      This session has no rallies yet. Detect or re-segment a source first.
    </p>
  </section>
{:else}
  <div class="relative">
    <!--
      onended replays instead of advancing, so playback loops inside the
      span. label.html did this deliberately: the boundary is part of what
      is being judged, and letting the video run on means judging the next
      rally by accident -- which matters most for the end_late case these
      flags exist to capture.

      `speed` is deliberately left at its default of 1. At 2x a reach and a
      swing are not reliably distinguishable, and a wrong label is worse
      than a slow pass.
    -->
    <VideoDeck
      bind:this={deck}
      src={srcFor(current.source_id)}
      startMs={current.det_start_ms}
      endMs={current.det_end_ms}
      onended={() => deck?.replay()}
    />
    <div
      class="pointer-events-none absolute left-2 top-2 rounded bg-black/60 px-2 py-1
             font-mono text-xs tabular-nums text-neutral-200"
    >
      {stats.index + 1} / {stats.total} · {stats.labelled} labelled
    </div>
  </div>

  <!--
    Confidence is deliberately not rendered. Full blinding would be
    pointless -- you already know these spans are detector output -- but
    the highest-confidence window in the existing fixture is a false
    positive (someone walking past the lens), so seeing the number before
    judging is a bias with no compensating benefit.
  -->
  <div class="mt-2 font-mono text-xs text-neutral-400">
    {formatTs(current.det_start_ms)} ·
    {formatDuration(current.det_end_ms - current.det_start_ms)}
  </div>

  <div class="mt-3 flex flex-wrap gap-2">
    {#each Object.entries(VERDICT_KEYS) as [key, v] (v)}
      <button
        class="rounded border px-3 py-1 font-mono text-sm
               {verdict === v
          ? 'border-blue-500 bg-blue-500/20 text-blue-200'
          : 'border-neutral-700 text-neutral-300 hover:bg-neutral-800'}"
        onclick={() => apply(controller?.setVerdict(v) ?? null)}
      >
        {v} <span class="text-neutral-500">{key}</span>
      </button>
    {/each}
  </div>

  <div class="mt-2 flex flex-wrap gap-2">
    {#each FLAG_ORDER as f (f)}
      <button
        disabled={!flagsEnabled}
        class="rounded border px-3 py-1 font-mono text-xs disabled:opacity-30
               {flags.includes(f)
          ? 'border-yellow-500 bg-yellow-500/20 text-yellow-200'
          : 'border-neutral-700 text-neutral-300 hover:bg-neutral-800'}"
        onclick={() => apply(controller?.toggleFlag(f) ?? null)}
      >
        {FLAG_LABELS[f]}
      </button>
    {/each}
  </div>

  <p class="mt-4 font-mono text-xs text-neutral-500">
    1 clean · 2 no play · 3 partly · 4 unsure · Q/W start · O/P end · R replay · ← →
    move · U undo · L back to queue
  </p>
{/if}

{#if toaster.toasts.length > 0}
  <div class="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2">
    {#each toaster.toasts as t (t.id)}
      <div class="rounded bg-red-500/90 px-3 py-2 text-sm text-white shadow-lg">
        {t.message}
      </div>
    {/each}
  </div>
{/if}
```

- [ ] **Step 7: Wire `l` into QueueMode**

In `web/src/components/QueueMode.svelte`, add to `Props`:

```ts
    /** Enter label mode. Separate from review: a verdict is a note about the
     * detector, not a decision about the clip, so it deliberately does not
     * touch star/reject or the session's review status. */
    onopen_label: () => void
```

destructure it:

```ts
  let { detail, onopen_timeline, onopen_label }: Props = $props()
```

add to the `switch` in `onKey`, after the `'t'` case:

```ts
      case 'l':
      case 'L':
        onopen_label()
        break
```

and extend the help line to:

```svelte
  <p class="mt-4 font-mono text-xs text-neutral-500">
    S star · X reject (again to undo) · R replay · ← back · → next · U undo · 1/2/3 speed · T timeline · L label
  </p>
```

- [ ] **Step 8: Wire the third mode into Session**

In `web/src/routes/Session.svelte`:

Import it beside the others:

```ts
  import LabelMode from '../components/LabelMode.svelte'
```

Widen the mode union (line 18):

```ts
  let mode = $state<'queue' | 'timeline' | 'label'>('queue')
```

Add next to `closeTimeline`:

```ts
  // Unlike closeTimeline, this does NOT refetch. Label mode writes only to
  // rally_labels; it never changes a rally's bounds, flags or review status,
  // so `detail` cannot have gone stale and a refetch would only discard
  // QueueMode's undo stack by bumping rallyRevision.
  function closeLabel() {
    mode = 'queue'
  }
```

Extend the keyed block:

```svelte
  {#key rallyRevision}
    {#if mode === 'queue'}
      <QueueMode {detail} onopen_timeline={openTimeline} onopen_label={() => (mode = 'label')} />
    {:else if mode === 'label'}
      <LabelMode {detail} onclose={closeLabel} />
    {:else if focusedRallyId}
      <TimelineMode
        {detail}
        rallyId={focusedRallyId}
        initialRallies={timelineRallies ?? undefined}
        onclose={closeTimeline}
      />
    {/if}
  {/key}
```

- [ ] **Step 9: Type-check and run the whole frontend suite**

Run (from `web/`): `npm run check && npx vitest run`
Expected: svelte-check clean (0 errors, 0 warnings), all vitest files pass. `web/tests/library-setup-card.test.ts` and any harness test that mounts QueueMode will fail if `onopen_label` was not added everywhere QueueMode is constructed — fix those call sites by passing `onopen_label={() => {}}`.

- [ ] **Step 10: Build and verify by hand**

Run (from `web/`): `npm run build`
Then, in a second terminal: `bootleg --library /Volumes/SanDisk_2TB/BootlegVision serve`

Open a session with rallies, press `L`, and confirm:
- the clip loops inside its span rather than running on
- `1`–`4` set a verdict and the button highlights
- `Q`/`W`/`O`/`P` are greyed out until a verdict is set, and under `not_play`/`unsure`
- the counter increments as verdicts are set
- `L` or `Escape` returns to the queue
- reloading the page and re-entering label mode shows the verdicts you set

Then confirm the corpus landed:

```bash
bootleg --library /Volumes/SanDisk_2TB/BootlegVision labels export <source_id>
```

- [ ] **Step 11: Commit**

```bash
git add web/src/components/LabelMode.svelte web/src/components/QueueMode.svelte \
        web/src/routes/Session.svelte web/src/lib/api.ts web/src/lib/labels.ts \
        web/tests/labels.test.ts
git commit -m "feat(web): label mode

A third mode beside queue and timeline, entered with L. Playback loops
inside the span because the boundary is part of what is being judged;
speed control is gone because at 2x a reach and a swing are not reliably
distinguishable; confidence is hidden because the highest-confidence window
in the existing fixture is a false positive.

Closing it does not refetch the session -- label mode writes only to
rally_labels, so detail cannot have gone stale and a refetch would discard
QueueMode's undo stack for nothing."
```

---

## Post-implementation

Once Task 7 is verified by hand, update the two documents that describe how the detector is evaluated:

- `CLAUDE.md` — add `rally_labels` to the conventions section beside `replace_rallies`, noting that the corpus is anchored to detector spans and survives a re-segment, and that `bootleg labels score` is the scoring loop.
- `docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md` — its closing section asks for a second labelling pass. Add a line pointing at label mode as the way to produce one.

Both are documentation-only and belong in a single follow-up commit.
