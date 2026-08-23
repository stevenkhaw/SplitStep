# Reels, Builder and Preview Implementation Plan (Plan B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn cut clips into reels — a span-keyed reel data model, a `reel` job that concatenates clips with `-c copy` and verifies the result, a `/reels` list, a `/reels/:slug` builder with drag-to-reorder, and a preview that seeks the proxy.

**Architecture:** `reel_items` is replaced (migration 006) so it keys on `(source_id, start_ms, end_ms)` — the same thing `clip_relpath()` names on disk — never on `rally_id`, whose `ON DELETE CASCADE` in `001_init.sql` would empty every reel on the first re-segment. A `reel` job concat-demuxes the clips with `-c copy`, probes the output and falls back to a full re-encode if the duration does not match the sum of its inputs. The UI adds two hash routes over pure logic in `web/src/lib/`; preview reuses `VideoDeck` against the proxy rather than chaining clip files.

**Tech Stack:** Python 3.12 (sqlite3, FastAPI, ffmpeg/ffprobe, pytest), Svelte 5 runes + TypeScript + vitest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-21-clip-export-and-reels-design.md`. **Read §2, §5 and §6 before Task 1.** §2 records three defects it corrects in an older design document; that reasoning is load-bearing. This plan covers §5–§6 only; §3–§4 shipped as Plan A (`docs/superpowers/plans/2026-08-21-clip-export.md`).
- Python runs from the `splitstep` conda env by path: `~/miniconda3/envs/splitstep/bin/pytest`, `~/miniconda3/envs/splitstep/bin/ruff`.
- ruff line-length 100. **ruff defaults here are broader than the classic set** — `I001` (import ordering), `BLE001` (blind except), `PLW1510` (`subprocess.run` without `check=`) are active.
- `pytest` runs with `filterwarnings = ["error"]` — a new warning fails the suite.
- Migrations are numbered `.sql` applied by `PRAGMA user_version`. Add **`006_reel_items_by_span.sql`** and nothing else; `001`–`005` are applied to the user's real library and **must never be edited**. `migrate()` skips any file numbered `<= user_version`, so a duplicate number is silently ignored forever — this project has already lost a round to that. Before writing, run `ls splitstep/db/migrations/` and confirm `006` is free.
- **The locked clip profile may never change**: `mp4 · H.264 High · yuv420p · 3840×2160 · 30 fps CFR · CRF 20 · AAC 128k 48 kHz stereo`. Every reel concatenating clips depends on every clip sharing it exactly.
- **Never auto-enqueue encoding.** A button labelled "render" must not start half an hour of work. Render refuses while any clip is missing, naming the count; cutting stays an explicit, separate action.
- API routes are `def`, not `async def`.
- All frontend logic lives in `web/src/lib/`, never in a `.svelte` file — jsdom has no `<video>`.
- Drag-to-reorder is hand-rolled pointer dragging following `ZoomBand`/`QuadEditor`. **No new dependency.**
- Comments explain **why**, not what. This codebase carries long rationale comments on non-obvious calls; match that density.
- **Do not start or restart any server.** One runs on port 8420 against the user's real library.
- **Handlers take three arguments.** `Handler = Callable[[Library, dict, ProgressFn], None]` — every handler ends with `progress: ProgressFn = no_progress`. The CLI and tests call handlers directly with two.
- **`run_ffmpeg` gained `on_progress`/`total_ms`** and raises if `timeout` is passed alongside them. Nothing in this plan passes a timeout.
- **The concat demuxer does not validate anything.** Measured on ffmpeg 9.0.1 (spec §4.2): mismatched codec parameters exit 0 with empty stderr, and every input is read through the *first* clip's parameters. A duration check alone therefore cannot see a mismatched SAR or a missing audio stream — Task 2 adds a pre-flight comparison of the inputs for exactly that reason. Do not remove either check believing the other covers it.
- ffmpeg must be on PATH. YOLO is never run in tests; nothing here touches detection.
- **Baseline at `3e924c8` (master): 511 pytest, 326 vitest, ruff clean, svelte-check 0.** Any task ending below those counts deleted something.

---

## File Structure

**Python — created**

| File | Responsibility |
|---|---|
| `splitstep/db/migrations/006_reel_items_by_span.sql` | Replace `reel_items` with the span-keyed table |
| `splitstep/db/reels.py` | Pure SQL: reel CRUD, slug allocation, membership, ordering, dirty/rendered |
| `splitstep/media/concat.py` | `-c copy` concat + duration verification + re-encode fallback |
| `splitstep/reels.py` | Library-aware: resolve items to clip status + rally metadata, plan the cut |

**Python — modified**

| File | Change |
|---|---|
| `splitstep/media/probe.py` | extract `ffprobe_json()` so concat can read stream params |
| `splitstep/db/jobs.py` | `has_pending_reel()` |
| `splitstep/jobs/handlers.py` | `handle_reel`, `HANDLERS` entry, `handle_clip` tolerates a missing `rally_id` |
| `splitstep/api/routes.py` | Nine reel routes |

**Frontend — created**

| File | Responsibility |
|---|---|
| `web/src/lib/reorder.ts` | `moveItem`, `dropIndex` — pure reorder math |
| `web/src/lib/reels.ts` | `spanKey`, `mergeSpans`, `missingClipCount`, `renderBlockedReason`, `reelStateLabel` |
| `web/src/lib/reelPreview.ts` | `ReelPreviewController` — pure ordering/advance |
| `web/src/routes/Reels.svelte` | The `/reels` list + New reel |
| `web/src/routes/Reel.svelte` | The `/reels/:slug` builder |
| `web/src/components/ReelItemList.svelte` | Drag-reorder list shell over `reorder.ts` |
| `web/src/components/AddRalliesPicker.svelte` | Session → filter → checkboxes |
| `web/src/components/ReelPreview.svelte` | `VideoDeck` shell over `reelPreview.ts` |

**Frontend — modified:** `web/src/lib/router.svelte.ts`, `web/src/lib/types.ts`, `web/src/lib/api.ts`, `web/src/App.svelte`, `web/src/routes/Library.svelte`, `web/src/components/QueueMode.svelte`.

---

## Starting state

Plan A and its whole-branch follow-up are **merged to master at `3e924c8`** and the tree is clean. Branch off it.

Three things from that follow-up bear on this plan:

- **`make_clip` now conforms SAR and audio presence** — a de-anamorphizing scale before rotation with `setsar=1` last, and an `anullsrc` stereo track for a silent source. Both exist because `-c copy` silently ignores a mismatch rather than refusing it. Every reel here depends on that conformance.
- **The concat demuxer validates nothing** (spec §4.2, measured). See the Global Constraints note; Task 2 is built around it.
- **`find_orphan_clips` / `parse_clip_name`** already establish that anything reading `clips/` gates on the exact name pattern, never a bare glob. This plan resolves clips by exact path, which sidesteps it — Task 3 has the test that keeps it that way.

Verified on the live library at `/Volumes/SanDisk_2TB/SplitStep` before this plan was revised: all 24 cut clips are uniform — `h264 High / 3840x2160 / SAR 1:1 / yuv420p / 30fps`, `aac 48000 stereo`, no rotation side data. Task 2's pre-flight will pass on them and the first real render will take the `-c copy` path. (The 24 `._*` files beside them are macOS AppleDouble sidecars on exFAT, not encode temps; `parse_clip_name` already rejects them.)

**Out of scope:** a `splitstep reels` CLI. The spec's §6 is UI-only, and every reel operation has a route. `splitstep clips export` exists because cutting is slow and worth scripting; composing a reel is not.

---

### Task 1: The span-keyed `reel_items` table

**Files:**
- Create: `splitstep/db/migrations/006_reel_items_by_span.sql`
- Create: `splitstep/db/reels.py`
- Test: `tests/test_db_reels.py` (create)

**Interfaces:**
- Consumes: `splitstep.db.schema.migrate`, the `library`/`conn` fixtures in `tests/conftest.py`.
- Produces:
  - `slugify(name: str) -> str`
  - `unique_slug(conn, base: str) -> str`
  - `create_reel(conn, name: str) -> sqlite3.Row`
  - `get_reel(conn, reel_id: str) -> sqlite3.Row | None`
  - `get_reel_by_slug(conn, slug: str) -> sqlite3.Row | None`
  - `find_reel_by_name(conn, name: str) -> sqlite3.Row | None`
  - `list_reels(conn) -> list[sqlite3.Row]` (each row carries `item_count`)
  - `list_items(conn, reel_id: str) -> list[sqlite3.Row]`
  - `add_items(conn, reel_id: str, spans: list[tuple[str, int, int]]) -> int`
  - `remove_item(conn, reel_id: str, source_id: str, start_ms: int, end_ms: int) -> bool`
  - `set_order(conn, reel_id: str, spans: list[tuple[str, int, int]]) -> None`
  - `mark_dirty(conn, reel_id: str) -> None`
  - `mark_rendered(conn, reel_id: str, rendered_path: str) -> None`

- [ ] **Step 1: Confirm the migration number is free**

```bash
ls splitstep/db/migrations/
```

Expected: `001_init.sql 002_rotation.sql 003_rally_labels.sql 004_label_retraction.sql 005_point_flag.sql` — and no `006_*`. If a `006` already exists, **stop and ask**: `migrate()` skips any file numbered `<= user_version`, so a second `006` would be ignored forever and the table would silently keep its cascade.

- [ ] **Step 2: Write the migration**

Create `splitstep/db/migrations/006_reel_items_by_span.sql`:

```sql
-- reel_items keyed on the SPAN, not on a rally.
--
-- 001_init.sql declared this table with
--   rally_id TEXT NOT NULL REFERENCES rallies(id) ON DELETE CASCADE
-- and replace_rallies deletes EVERY rally for a source on each threshold
-- sweep. That cascade would therefore have silently emptied every reel on
-- the first re-segment -- the same trap rally_labels was deliberately built
-- to avoid (see its "deliberately carries no foreign key" comment in 003),
-- walked into by the table declared right next to it. The table has never
-- held a row, so this is a replacement, not a data migration.
--
-- Cascading on source_id IS correct, and the asymmetry is the whole point:
-- delete the source and the footage is gone, so the clip is meaningless.
-- Delete a rally and nothing about the footage changed -- a rally is a guess
-- the detector re-makes every sweep, and the clip it named is still on disk.
--
-- A reel is an ordered list of CLIPS, and a clip is a span of a source,
-- which is also exactly how clip_relpath() names the file. Keying on the
-- span means an item and its file agree by construction, with no join
-- through a row that a sweep is free to delete.
DROP TABLE reel_items;

CREATE TABLE reel_items (
  reel_id   TEXT NOT NULL REFERENCES reels(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  start_ms  INTEGER NOT NULL,
  end_ms    INTEGER NOT NULL,
  position  INTEGER NOT NULL,
  PRIMARY KEY (reel_id, source_id, start_ms, end_ms)
);

-- position is not unique and deliberately not constrained to be: set_order
-- rewrites every row in one pass, and a UNIQUE(reel_id, position) would
-- force the two-phase negative-placeholder dance _renumber needs for
-- rallies.idx. The primary key already stops a span appearing twice, which
-- is the invariant that matters.
CREATE INDEX idx_reel_items_order ON reel_items(reel_id, position);
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_db_reels.py`:

```python
import pytest

from splitstep.db.reels import (
    add_items,
    create_reel,
    find_reel_by_name,
    get_reel,
    get_reel_by_slug,
    list_items,
    list_reels,
    mark_dirty,
    mark_rendered,
    remove_item,
    set_order,
    slugify,
    unique_slug,
)
from splitstep.db.rallies import replace_rallies
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


def _spans(conn, reel_id):
    return [(r["source_id"], r["start_ms"], r["end_ms"]) for r in list_items(conn, reel_id)]


def test_reel_items_has_no_rally_foreign_key(conn):
    # The defect this migration exists to fix. 001 declared rally_id with
    # ON DELETE CASCADE; a re-segment would have emptied every reel.
    fks = conn.execute("PRAGMA foreign_key_list(reel_items)").fetchall()
    assert {fk["table"] for fk in fks} == {"reels", "sources"}
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(reel_items)")}
    assert "rally_id" not in cols
    assert {"reel_id", "source_id", "start_ms", "end_ms", "position"} <= cols


def test_a_resegment_leaves_the_reel_intact(conn, seeded):
    # The actual regression test, not just a schema assertion: build a reel
    # from a rally's span, then blow every rally away the way a threshold
    # sweep does, and the reel must still hold its item.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1000, 5000, 0.8)])
    reel = create_reel(conn, "2026-08-18 points")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 5000)])

    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1200, 4800, 0.7)])

    assert _spans(conn, reel["id"]) == [(seeded["source_id"], 1000, 5000)]


def test_deleting_the_source_does_cascade(conn, seeded):
    # The other half of the asymmetry: no footage, no clip, no item.
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 5000)])
    conn.execute("DELETE FROM sources WHERE id = ?", (seeded["source_id"],))
    conn.commit()
    assert _spans(conn, reel["id"]) == []


def test_slugify_lowercases_and_hyphenates():
    assert slugify("2026-08-18 points") == "2026-08-18-points"
    assert slugify("  Best of  July!  ") == "best-of-july"
    # A name with nothing sluggable still needs a slug, because reels.slug is
    # NOT NULL UNIQUE and the name is the user's to choose.
    assert slugify("!!!") == "reel"


def test_unique_slug_suffixes_on_collision(conn):
    create_reel(conn, "2026-08-18 points")
    assert unique_slug(conn, "2026-08-18-points") == "2026-08-18-points-2"


def test_a_repeated_name_gets_its_own_slug(conn):
    first = create_reel(conn, "2026-08-18 points")
    second = create_reel(conn, "2026-08-18 points")
    assert first["slug"] == "2026-08-18-points"
    assert second["slug"] == "2026-08-18-points-2"
    assert first["id"] != second["id"]


def test_a_new_reel_is_dirty_and_unrendered(conn):
    reel = create_reel(conn, "r")
    assert reel["dirty"] == 1
    assert reel["rendered_path"] is None
    assert reel["rendered_at"] is None


def test_add_items_appends_in_order(conn, seeded):
    reel = create_reel(conn, "r")
    src = seeded["source_id"]
    assert add_items(conn, reel["id"], [(src, 3000, 4000), (src, 1000, 2000)]) == 2
    # Appended in the order given, NOT sorted -- the caller decides order
    # (the session-set route passes rallies in chronological order); once a
    # human reorders, sorting here would silently undo it.
    assert _spans(conn, reel["id"]) == [(src, 3000, 4000), (src, 1000, 2000)]


def test_add_items_is_additive_and_skips_duplicates(conn, seeded):
    reel = create_reel(conn, "r")
    src = seeded["source_id"]
    add_items(conn, reel["id"], [(src, 3000, 4000), (src, 1000, 2000)])
    set_order(conn, reel["id"], [(src, 1000, 2000), (src, 3000, 4000)])

    added = add_items(conn, reel["id"], [(src, 1000, 2000), (src, 5000, 6000)])

    # One genuinely new span appended; the hand-ordering above untouched and
    # nothing removed. Overwriting membership would silently discard a manual
    # reorder -- the same class of mistake replace_rallies makes with
    # boundary edits, which already cost this project a 9.6-second rally.
    assert added == 1
    assert _spans(conn, reel["id"]) == [
        (src, 1000, 2000), (src, 3000, 4000), (src, 5000, 6000),
    ]


def test_add_items_ignores_a_duplicate_within_one_call(conn, seeded):
    reel = create_reel(conn, "r")
    src = seeded["source_id"]
    assert add_items(conn, reel["id"], [(src, 1000, 2000), (src, 1000, 2000)]) == 1


def test_remove_item(conn, seeded):
    reel = create_reel(conn, "r")
    src = seeded["source_id"]
    add_items(conn, reel["id"], [(src, 1000, 2000), (src, 3000, 4000)])
    assert remove_item(conn, reel["id"], src, 1000, 2000) is True
    assert remove_item(conn, reel["id"], src, 1000, 2000) is False
    assert _spans(conn, reel["id"]) == [(src, 3000, 4000)]


def test_remove_item_leaves_positions_ordered(conn, seeded):
    # Positions may gap after a removal; list_items must still be stable and
    # a later add must land at the end, not on top of a surviving row.
    reel = create_reel(conn, "r")
    src = seeded["source_id"]
    add_items(conn, reel["id"], [(src, 1000, 2000), (src, 3000, 4000), (src, 5000, 6000)])
    remove_item(conn, reel["id"], src, 3000, 4000)
    add_items(conn, reel["id"], [(src, 7000, 8000)])
    assert _spans(conn, reel["id"]) == [
        (src, 1000, 2000), (src, 5000, 6000), (src, 7000, 8000),
    ]


def test_set_order_rewrites_positions(conn, seeded):
    reel = create_reel(conn, "r")
    src = seeded["source_id"]
    add_items(conn, reel["id"], [(src, 1000, 2000), (src, 3000, 4000), (src, 5000, 6000)])
    set_order(conn, reel["id"], [(src, 5000, 6000), (src, 1000, 2000), (src, 3000, 4000)])
    assert _spans(conn, reel["id"]) == [
        (src, 5000, 6000), (src, 1000, 2000), (src, 3000, 4000),
    ]


def test_set_order_refuses_a_list_that_is_not_the_membership(conn, seeded):
    # A reorder that adds or drops a span is a bug in the caller, and
    # applying it partially would leave the reel holding an order that
    # describes something other than what it contains.
    reel = create_reel(conn, "r")
    src = seeded["source_id"]
    add_items(conn, reel["id"], [(src, 1000, 2000), (src, 3000, 4000)])
    with pytest.raises(ValueError):
        set_order(conn, reel["id"], [(src, 1000, 2000)])
    with pytest.raises(ValueError):
        set_order(conn, reel["id"], [(src, 1000, 2000), (src, 3000, 4000), (src, 9, 10)])
    assert _spans(conn, reel["id"]) == [(src, 1000, 2000), (src, 3000, 4000)]


def test_mark_rendered_then_dirty(conn):
    reel = create_reel(conn, "r")
    mark_rendered(conn, reel["id"], "reels/r.mp4")
    row = get_reel(conn, reel["id"])
    assert row["dirty"] == 0
    assert row["rendered_path"] == "reels/r.mp4"
    assert row["rendered_at"] is not None

    mark_dirty(conn, reel["id"])
    row = get_reel(conn, reel["id"])
    assert row["dirty"] == 1
    # rendered_path survives: the file is still on disk and still playable,
    # it is merely out of date. Clearing it would make "re-render" and
    # "never rendered" indistinguishable in the list.
    assert row["rendered_path"] == "reels/r.mp4"


def test_list_reels_carries_an_item_count(conn, seeded):
    empty = create_reel(conn, "empty")
    full = create_reel(conn, "full")
    add_items(conn, full["id"], [(seeded["source_id"], 1000, 2000)])
    by_slug = {r["slug"]: r["item_count"] for r in list_reels(conn)}
    assert by_slug == {"empty": 0, "full": 1}


def test_lookup_helpers(conn):
    reel = create_reel(conn, "2026-08-18 points")
    assert get_reel_by_slug(conn, "2026-08-18-points")["id"] == reel["id"]
    assert get_reel_by_slug(conn, "nope") is None
    assert find_reel_by_name(conn, "2026-08-18 points")["id"] == reel["id"]
    assert find_reel_by_name(conn, "nope") is None
```

- [ ] **Step 4: Run the tests to verify they fail**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_db_reels.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'splitstep.db.reels'`.

- [ ] **Step 5: Write `splitstep/db/reels.py`**

```python
import re
import sqlite3
import uuid
from datetime import UTC, datetime

Span = tuple[str, int, int]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def slugify(name: str) -> str:
    """A URL-safe slug for `name`, as the /reels/:slug route spells it.

    Falls back to "reel" for a name with nothing sluggable in it (an emoji,
    punctuation alone): reels.slug is NOT NULL UNIQUE and the name is the
    user's to choose, so an empty slug would turn a legal name into a 500.
    Uniqueness is unique_slug's job, not this function's -- keeping this one
    pure means the route can show a preview of the slug without a database.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "reel"


def unique_slug(conn: sqlite3.Connection, base: str) -> str:
    """`base`, or `base-2`, `base-3` ... if it is already taken.

    reels.slug is UNIQUE but a NAME is free to repeat -- two sessions'
    "points" reels, or a hand-made reel that happens to slug the same as a
    generated one. The suffix is what keeps a legal name from being refused
    over a URL detail the user never chose.
    """
    slug = base
    n = 1
    while conn.execute("SELECT 1 FROM reels WHERE slug = ?", (slug,)).fetchone():
        n += 1
        slug = f"{base}-{n}"
    return slug


def create_reel(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
    reel_id = uuid.uuid4().hex
    slug = unique_slug(conn, slugify(name))
    conn.execute(
        "INSERT INTO reels (id,name,slug,dirty,created_at) VALUES (?,?,?,1,?)",
        (reel_id, name, slug, _now()),
    )
    conn.commit()
    return get_reel(conn, reel_id)


def get_reel(conn: sqlite3.Connection, reel_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM reels WHERE id = ?", (reel_id,)).fetchone()


def get_reel_by_slug(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM reels WHERE slug = ?", (slug,)).fetchone()


def find_reel_by_name(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    """The oldest reel with exactly this name, or None.

    How the session-set buttons find the reel to merge into on a second
    click (see §6.2). Oldest rather than newest so repeated clicking keeps
    converging on one reel instead of walking down a chain of near-duplicates
    a slug collision created.
    """
    return conn.execute(
        "SELECT * FROM reels WHERE name = ? ORDER BY created_at, id LIMIT 1", (name,)
    ).fetchone()


def list_reels(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every reel, newest first, each carrying its item_count.

    One LEFT JOIN rather than a count query per reel: the list page renders
    the count on every row, and N+1 queries behind a route that already runs
    on a shared worker thread is the shape to avoid.
    """
    return conn.execute(
        "SELECT r.*, COUNT(i.reel_id) AS item_count FROM reels r"
        " LEFT JOIN reel_items i ON i.reel_id = r.id"
        " GROUP BY r.id ORDER BY r.created_at DESC, r.id"
    ).fetchall()


def list_items(conn: sqlite3.Connection, reel_id: str) -> list[sqlite3.Row]:
    # position, then the span, so a reel whose positions gapped or tied
    # (removal leaves gaps by design) still renders in a stable order rather
    # than in whatever sqlite happens to return.
    return conn.execute(
        "SELECT * FROM reel_items WHERE reel_id = ?"
        " ORDER BY position, start_ms, end_ms, source_id",
        (reel_id,),
    ).fetchall()


def _keys(conn: sqlite3.Connection, reel_id: str) -> set[Span]:
    return {
        (r["source_id"], r["start_ms"], r["end_ms"]) for r in list_items(conn, reel_id)
    }


def add_items(conn: sqlite3.Connection, reel_id: str, spans: list[Span]) -> int:
    """Append `spans` that are not already in the reel. Returns how many.

    Additive by contract, never a replacement: existing entries and the order
    a human dragged them into are untouched, and nothing is removed. This is
    §6.2's second-click behaviour, and the reason it is not "set membership"
    is that overwriting would silently discard a manual reorder.

    Deliberately NOT `INSERT OR IGNORE`: an ignored row would still have
    consumed the position counter, leaving gaps that read as an ordering the
    user never made. Filtering first means every position handed out lands.
    """
    existing = _keys(conn, reel_id)
    row = conn.execute(
        "SELECT COALESCE(MAX(position), -1) AS m FROM reel_items WHERE reel_id = ?",
        (reel_id,),
    ).fetchone()
    position = row["m"] + 1

    added = 0
    try:
        for source_id, start_ms, end_ms in spans:
            key = (source_id, start_ms, end_ms)
            # `existing` is updated as we go so a span repeated WITHIN one
            # call is skipped too -- the picker can hand us the same rally
            # twice, and a PRIMARY KEY violation mid-loop would abort an
            # otherwise good batch.
            if key in existing:
                continue
            existing.add(key)
            conn.execute(
                "INSERT INTO reel_items (reel_id,source_id,start_ms,end_ms,position)"
                " VALUES (?,?,?,?,?)",
                (reel_id, source_id, start_ms, end_ms, position),
            )
            position += 1
            added += 1
        if added:
            _mark_dirty(conn, reel_id)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return added


def remove_item(
    conn: sqlite3.Connection, reel_id: str, source_id: str, start_ms: int, end_ms: int
) -> bool:
    """Drop one span. Returns whether it was there.

    Positions are left gapped rather than compacted: nothing reads position
    as a rank, only as a sort key, and a compaction pass would be a second
    writer of the column for no gain. add_items appends past MAX(position),
    so a gap can never collide with a later insert.
    """
    cur = conn.execute(
        "DELETE FROM reel_items WHERE reel_id = ? AND source_id = ?"
        " AND start_ms = ? AND end_ms = ?",
        (reel_id, source_id, start_ms, end_ms),
    )
    if cur.rowcount:
        _mark_dirty(conn, reel_id)
    conn.commit()
    return cur.rowcount > 0


def set_order(conn: sqlite3.Connection, reel_id: str, spans: list[Span]) -> None:
    """Rewrite every position from the given order.

    Refuses a list that is not exactly the reel's current membership. A
    reorder that adds or drops a span is a bug in the caller (a stale client
    list racing a removal, most likely), and applying it partially would
    leave the reel holding an order describing something other than what it
    contains -- silently, since the UI renders whatever comes back.

    A single pass suffices, unlike rallies._renumber's two-phase dance:
    position carries no UNIQUE constraint here (see the migration), so an
    intermediate state where two rows briefly share a value is legal.
    """
    current = _keys(conn, reel_id)
    given = list(spans)
    if len(given) != len(current) or set(given) != current:
        raise ValueError(
            f"reorder must list exactly the reel's {len(current)} item(s), got {len(given)}"
        )
    try:
        for position, (source_id, start_ms, end_ms) in enumerate(given):
            conn.execute(
                "UPDATE reel_items SET position = ? WHERE reel_id = ? AND source_id = ?"
                " AND start_ms = ? AND end_ms = ?",
                (position, reel_id, source_id, start_ms, end_ms),
            )
        _mark_dirty(conn, reel_id)
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def _mark_dirty(conn: sqlite3.Connection, reel_id: str) -> None:
    """Set dirty WITHOUT committing -- for callers already inside a transaction."""
    conn.execute("UPDATE reels SET dirty = 1 WHERE id = ?", (reel_id,))


def mark_dirty(conn: sqlite3.Connection, reel_id: str) -> None:
    _mark_dirty(conn, reel_id)
    conn.commit()


def mark_rendered(conn: sqlite3.Connection, reel_id: str, rendered_path: str) -> None:
    """Record a successful render and clear dirty.

    rendered_path is library-relative, like rallies.clip_path: the drive
    mounts at a different point on each machine, so an absolute path stored
    here would be wrong the first time the library moves.
    """
    conn.execute(
        "UPDATE reels SET rendered_path = ?, rendered_at = ?, dirty = 0 WHERE id = ?",
        (rendered_path, _now(), reel_id),
    )
    conn.commit()
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_db_reels.py -q
```

Expected: PASS, 17 tests.

- [ ] **Step 7: Run the full suite and the linter**

```bash
~/miniconda3/envs/splitstep/bin/pytest -q && ~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
```

Expected: all green. Nothing else reads `reel_items` yet, so a failure here means the `DROP TABLE` hit something unexpected — read the error rather than adjusting the migration.

- [ ] **Step 8: Commit**

```bash
git add splitstep/db/migrations/006_reel_items_by_span.sql splitstep/db/reels.py tests/test_db_reels.py
git commit -m "feat(reels): key reel_items on the span, not the rally"
```

---

### Task 2: `-c copy` concat, guarded by a pre-flight parameter check and a duration check

**Files:**
- Modify: `splitstep/media/probe.py` (extract `ffprobe_json`)
- Create: `splitstep/media/concat.py`
- Test: `tests/test_concat.py` (create)

**Why there are two checks, not one.** §5.1 specifies the duration check, and it stays exactly as written. But §4.2's measured correction — added after §5.1 — says the concat demuxer does **not** refuse mismatched codec parameters on ffmpeg 9.0.1: it exits 0 with empty stderr and reads every input through the *first* clip's parameters. That produces three failure modes and the duration check sees only one of them:

| Failure | Effect on the reel | Duration check |
|---|---|---|
| Later inputs silently dropped | short by whole clips | catches it |
| Mismatched sample aspect ratio | full length, wrong-shaped picture | **blind** |
| One input carries no audio stream | picture runs on, audio stops early | **blind** |

Container duration is video duration in both blind cases, so a pre-flight comparison of the inputs is the only thing that can see them. It is the loud refusal ffmpeg declines to give.

**Interfaces:**
- Consumes: `splitstep.media.transcode.run_ffmpeg` / `CLIP_FPS` / `CLIP_CRF` / `ProgressFn` / `TranscodeError`, `splitstep.media.probe.probe`.
- Produces:
  - `ffprobe_json(path: Path, timeout: float | None = None) -> dict` (in `probe.py`)
  - `ConcatError(Exception)`
  - `ClipParams` frozen dataclass
  - `clip_params(path: Path) -> ClipParams`
  - `divergences(paths: list[Path]) -> list[str]`
  - `tolerance_ms(n_inputs: int) -> int`
  - `concat_clips(paths: list[Path], dst: Path, on_progress: ProgressFn | None = None) -> str` — returns `"copy"` or `"reencode"`

- [ ] **Step 1: Extract `ffprobe_json` in `splitstep/media/probe.py`**

`probe()` already shells out to `ffprobe -show_format -show_streams` and then throws the raw JSON away. Concat needs different fields out of that same document (`pix_fmt`, `profile`, audio rate and channels), and a second ffprobe implementation in this codebase would be two things to keep in agreement. Pure extraction, no behaviour change.

Replace the invocation block at the top of `probe()` with a call to a new public helper declared just above it:

```python
def ffprobe_json(path: Path, timeout: float | None = None) -> dict:
    """ffprobe's `-show_format -show_streams` document for `path`.

    Public and separate from `probe()` because two callers ask different
    questions of the same document. `probe()` answers media-level facts
    (duration, display size, rotation) and normalises them into MediaInfo;
    `concat.clip_params` needs raw stream-level codec parameters that
    MediaInfo deliberately does not carry, because only the concat demuxer
    cares about them. One subprocess implementation, one place where a
    missing ffprobe or a spun-down drive is turned into a ProbeError.
    """
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, check=False, timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ProbeError(
            "ffprobe not found on PATH. Install it: brew install ffmpeg"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out after {timeout}s for {path}") from exc
    if proc.returncode != 0:
        raise ProbeError(f"ffprobe failed for {path}: {proc.stderr.strip()}")
    return json.loads(proc.stdout or "{}")
```

`probe()` then begins:

```python
    data = ffprobe_json(path, timeout)
    streams = data.get("streams", [])
```

Everything from `streams = data.get("streams", [])` onward is unchanged. `tests/test_probe.py` must pass untouched — that is the check that this was a pure extraction.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_concat.py`:

```python
import subprocess

import pytest

from splitstep.media.concat import (
    ConcatError,
    clip_params,
    concat_clips,
    divergences,
    tolerance_ms,
)
from splitstep.media.probe import probe


def _clip(path, seconds=1.0, size="320x240", sar=None, silent=False, crf=23):
    """A clip that shares one profile with its siblings unless told otherwise.

    Not the locked 4K profile: these tests are about the concat mechanism,
    and encoding 3840x2160 repeatedly would make the suite unusable. What
    matters is that the inputs match EACH OTHER, which is exactly the
    precondition -c copy has in production.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration={seconds}"]
    if not silent:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    vf = f"setsar={sar}" if sar else "setsar=1"
    args += ["-vf", vf, "-c:v", "libx264", "-profile:v", "high",
             "-pix_fmt", "yuv420p", "-crf", str(crf), "-r", "30"]
    if not silent:
        args += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
    args += ["-shortest", str(path)]
    subprocess.run(args, check=True, capture_output=True)
    return path


def test_clip_params_reads_the_parameters_c_copy_assumes(tmp_path):
    params = clip_params(_clip(tmp_path / "a.mp4"))
    assert (params.codec, params.width, params.height) == ("h264", 320, 240)
    assert params.sample_aspect_ratio == "1:1"
    assert params.pix_fmt == "yuv420p"
    assert (params.audio_codec, params.audio_sample_rate, params.audio_channels) == (
        "aac", "48000", 2,
    )


def test_clip_params_reports_a_missing_audio_stream(tmp_path):
    params = clip_params(_clip(tmp_path / "a.mp4", silent=True))
    assert params.audio_codec is None
    assert params.audio_channels is None


def test_matching_clips_have_no_divergences(tmp_path):
    parts = [_clip(tmp_path / f"{i}.mp4") for i in range(3)]
    assert divergences(parts) == []


def test_a_single_clip_cannot_diverge(tmp_path):
    assert divergences([_clip(tmp_path / "a.mp4")]) == []


def test_divergences_names_a_mismatched_sample_aspect(tmp_path):
    # The failure ffmpeg swallows: the clip plays at the wrong shape for its
    # whole duration and the output's length is unchanged, so nothing
    # downstream can see it.
    parts = [_clip(tmp_path / "a.mp4"), _clip(tmp_path / "b.mp4", sar="2/1")]
    reported = divergences(parts)
    assert len(reported) == 1
    assert "b.mp4" in reported[0]
    assert "sample_aspect_ratio" in reported[0]


def test_divergences_names_a_missing_audio_stream(tmp_path):
    # The other silent failure: the reel's audio stops early while the
    # picture runs on.
    parts = [_clip(tmp_path / "a.mp4"), _clip(tmp_path / "b.mp4", silent=True)]
    reported = divergences(parts)
    assert any("audio_codec" in r for r in reported)


def test_divergences_names_a_mismatched_frame_size(tmp_path):
    parts = [_clip(tmp_path / "a.mp4"), _clip(tmp_path / "b.mp4", size="640x480")]
    reported = divergences(parts)
    assert any("width" in r for r in reported)


def test_divergences_compares_against_the_first_clip(tmp_path):
    # Against the FIRST input, not against the locked profile's constants:
    # the first clip is what ffmpeg actually reads every other input
    # through. A library cut uniformly at some other profile concatenates
    # correctly, and refusing it would be a rule about our constants rather
    # than about the output.
    parts = [_clip(tmp_path / "a.mp4", size="640x480"),
             _clip(tmp_path / "b.mp4", size="640x480")]
    assert divergences(parts) == []


def test_concat_sums_the_durations(tmp_path):
    parts = [_clip(tmp_path / f"{i}.mp4") for i in range(3)]
    dst = tmp_path / "reel.mp4"

    assert concat_clips(parts, dst) == "copy"

    assert dst.exists()
    total = sum(probe(p).duration_ms for p in parts)
    assert abs(probe(dst).duration_ms - total) <= tolerance_ms(3)


def test_a_single_input_still_concatenates(tmp_path):
    # A one-item reel is a real thing a user can build.
    dst = tmp_path / "reel.mp4"
    assert concat_clips([_clip(tmp_path / "a.mp4")], dst) == "copy"
    assert dst.exists()


def test_no_inputs_is_refused(tmp_path):
    with pytest.raises(ConcatError):
        concat_clips([], tmp_path / "reel.mp4")


def test_a_missing_input_is_refused(tmp_path):
    with pytest.raises(ConcatError):
        concat_clips([tmp_path / "nope.mp4"], tmp_path / "reel.mp4")


def test_a_nonconforming_input_skips_the_copy_entirely(tmp_path, caplog):
    """The pre-flight, end to end.

    -c copy is never attempted, because on ffmpeg 9.0.1 it would SUCCEED
    and produce a reel whose second clip plays at the wrong shape -- exit 0,
    empty stderr, correct duration. Going straight to a re-encode is the
    only outcome that yields a correct file.
    """
    parts = [_clip(tmp_path / "a.mp4"), _clip(tmp_path / "b.mp4", sar="2/1")]
    dst = tmp_path / "reel.mp4"

    with caplog.at_level("WARNING"):
        assert concat_clips(parts, dst) == "reencode"

    assert dst.exists()
    assert any("sample_aspect_ratio" in r.getMessage() for r in caplog.records)
    # The re-encode's own output is uniform, which is the point.
    assert clip_params(dst).sample_aspect_ratio == "1:1"


def test_a_short_copy_falls_back_to_a_reencode(tmp_path, monkeypatch):
    """The other half: inputs that DO conform, but a copy that came out short.

    Simulated by forcing the copy pass to write only the first input --
    what is under test is the DECISION, which cannot be provoked on demand
    with real ffmpeg.
    """
    import splitstep.media.concat as concat_mod

    parts = [_clip(tmp_path / f"{i}.mp4") for i in range(3)]
    dst = tmp_path / "reel.mp4"
    real_run = concat_mod.run_ffmpeg
    calls = []

    def fake_run(args, timeout=None, on_progress=None, total_ms=None):
        calls.append(args)
        if "copy" in args:
            real_run(["-i", str(parts[0]), "-c", "copy", args[-1]])
            return
        real_run(args)

    monkeypatch.setattr(concat_mod, "run_ffmpeg", fake_run)

    assert concat_clips(parts, dst) == "reencode"

    assert len(calls) == 2
    total = sum(probe(p).duration_ms for p in parts)
    assert abs(probe(dst).duration_ms - total) <= tolerance_ms(3) * 4


def test_a_failed_copy_leaves_no_partial_output(tmp_path, monkeypatch):
    import splitstep.media.concat as concat_mod

    parts = [_clip(tmp_path / "a.mp4")]
    dst = tmp_path / "reel.mp4"

    def fake_run(args, timeout=None, on_progress=None, total_ms=None):
        raise concat_mod.TranscodeError("boom")

    monkeypatch.setattr(concat_mod, "run_ffmpeg", fake_run)

    with pytest.raises(concat_mod.TranscodeError):
        concat_clips(parts, dst)

    # Not merely absent: no temp and no concat listing left behind either.
    assert not dst.exists()
    assert list(tmp_path.glob(".*")) == []


def test_an_existing_reel_is_overwritten(tmp_path):
    # Idempotent by overwrite, like every other handler: re-rendering after
    # a reorder replaces the file rather than refusing or appending.
    dst = tmp_path / "reel.mp4"
    dst.write_bytes(b"stale")
    concat_clips([_clip(tmp_path / "a.mp4")], dst)
    assert dst.read_bytes()[:4] != b"stal"


def test_a_path_containing_a_quote_is_escaped(tmp_path):
    # The library root is a user-chosen path. The concat demuxer's
    # `file '...'` directive ends at the first unescaped quote.
    odd = tmp_path / "it's a drive"
    odd.mkdir()
    dst = tmp_path / "reel.mp4"
    assert concat_clips([_clip(odd / "a.mp4")], dst) == "copy"
    assert dst.exists()


def test_progress_is_reported_during_a_reencode(tmp_path):
    # The re-encode is the slow path -- minutes on a real reel -- and it is
    # the one worth a badge. handle_reel passes the job's reporter through.
    parts = [_clip(tmp_path / "a.mp4"), _clip(tmp_path / "b.mp4", sar="2/1")]
    seen: list[float] = []
    concat_clips(parts, tmp_path / "reel.mp4", on_progress=seen.append)
    assert seen
    assert seen == sorted(seen)
    assert seen[-1] == pytest.approx(1.0)


def test_tolerance_grows_with_the_input_count():
    assert tolerance_ms(1) < tolerance_ms(24)
    # Still far below the failure being caught: a reel silently missing even
    # its shortest point is seconds short, not milliseconds.
    assert tolerance_ms(24) < 2000
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_concat.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'splitstep.media.concat'`.

- [ ] **Step 4: Write `splitstep/media/concat.py`**

```python
import contextlib
import logging
import os
import uuid
from dataclasses import dataclass, fields
from pathlib import Path

from splitstep.media.probe import ffprobe_json, probe
from splitstep.media.transcode import (
    CLIP_CRF,
    CLIP_FPS,
    ProgressFn,
    TranscodeError,
    run_ffmpeg,
)

log = logging.getLogger(__name__)


class ConcatError(Exception):
    """A reel could not be concatenated."""


# A concat that succeeds still shifts the total by a frame or so per input:
# each clip's duration is a whole number of frames at 30 fps, and the muxer
# rounds edit lists and the final sample's duration independently. 40ms is a
# little over one frame at the locked rate.
_TOLERANCE_BASE_MS = 100
_TOLERANCE_PER_INPUT_MS = 40


def tolerance_ms(n_inputs: int) -> int:
    """How far the concatenated duration may sit from the sum of its inputs.

    Deliberately generous. The failure this catches is a reel quietly
    missing its last four points -- tens of seconds -- not a rounding
    remainder, so a tight bound would only buy false re-encodes on
    well-formed output. Being wrong the other way is cheap: the fallback
    costs a re-encode, and it is logged.
    """
    return _TOLERANCE_BASE_MS + _TOLERANCE_PER_INPUT_MS * max(0, n_inputs)


@dataclass(frozen=True)
class ClipParams:
    """The stream parameters `-c copy` silently assumes every input shares.

    This exists because ffmpeg does NOT check them. The design was written
    assuming the concat demuxer refuses streams whose codec parameters
    differ; measured on ffmpeg 9.0.1 it does not. It exits 0 with empty
    stderr and reads every input through the FIRST clip's parameters, so a
    mismatched sample aspect plays that clip at the wrong shape for its
    whole duration, and an input carrying no audio stream contributes no
    audio -- the reel's track stops early while the picture runs on.

    Neither of those changes the output's DURATION, so neither is visible to
    the duration check in concat_clips. Comparing the inputs up front is the
    only thing that can see them: it is the loud refusal ffmpeg declines to
    give.
    """

    codec: str
    profile: str
    width: int
    height: int
    sample_aspect_ratio: str
    pix_fmt: str
    frame_rate: str
    audio_codec: str | None
    audio_sample_rate: str | None
    audio_channels: int | None


def clip_params(path: Path) -> ClipParams:
    """Read the parameters `-c copy` cares about out of one clip."""
    data = ffprobe_json(path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ConcatError(f"No video stream in {path}")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return ClipParams(
        codec=video.get("codec_name", ""),
        profile=video.get("profile", ""),
        width=int(video["width"]),
        height=int(video["height"]),
        # ffprobe omits the field entirely for square pixels. Defaulting to
        # "1:1" rather than leaving it None is what makes a clip cut before
        # make_clip pinned SAR compare EQUAL to one cut after -- they really
        # are the same shape, and diverging over an absent tag would send
        # every pre-existing library down the re-encode path for nothing.
        sample_aspect_ratio=video.get("sample_aspect_ratio", "1:1"),
        pix_fmt=video.get("pix_fmt", ""),
        # r_frame_rate as ffprobe's raw string ("30/1"). Kept exact rather
        # than floated: mismatched rates break -c copy, and 30/1 against
        # 30000/1001 is precisely the difference a float comparison at any
        # tolerance would blur away.
        frame_rate=video.get("r_frame_rate", ""),
        audio_codec=None if audio is None else audio.get("codec_name"),
        audio_sample_rate=None if audio is None else audio.get("sample_rate"),
        audio_channels=None if audio is None else int(audio["channels"]),
    )


def divergences(paths: list[Path]) -> list[str]:
    """Every way a later clip's parameters differ from the first clip's.

    Compared against the FIRST clip rather than against the locked profile's
    constants on purpose: the first clip is what ffmpeg will actually read
    every other input through, so it is the thing they have to match. A
    library cut uniformly at some other profile still concatenates
    correctly, and refusing it would be a rule about our constants instead
    of about the output.

    Returns human-readable strings rather than a bool because the caller
    logs them: "b.mp4: sample_aspect_ratio is '2:1', expected '1:1'" is
    something a human can act on, and "inputs differ" is not.
    """
    if len(paths) < 2:
        return []
    first = clip_params(paths[0])
    reported: list[str] = []
    for path in paths[1:]:
        params = clip_params(path)
        if params == first:
            continue
        for field in fields(ClipParams):
            mine = getattr(params, field.name)
            theirs = getattr(first, field.name)
            if mine != theirs:
                reported.append(
                    f"{path.name}: {field.name} is {mine!r}, expected {theirs!r}"
                )
    return reported


def _escape(path: Path) -> str:
    """A path as the concat demuxer's `file '...'` directive spells it.

    The directive ends at the first unescaped quote, and its escape is the
    shell's: close, backslash-quote, reopen. Clip names are always
    NN-START-END.mp4 and carry none, but the library root is a user-chosen
    path on an external drive and may contain anything at all.
    """
    return str(path).replace("'", "'\\''")


def _reencode_args(base: list[str], tmp: Path) -> list[str]:
    """The fallback encode, at the locked profile's own settings.

    scale and pad are absent because every input already carries the locked
    frame -- or, when they do not, because the mismatch is exactly what sent
    us here and the first input's frame is what the demuxer imposes anyway.
    The RESULT is still a file that could itself be concatenated.
    """
    return [
        *base,
        "-r", str(CLIP_FPS),
        "-c:v", "libx264",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-crf", str(CLIP_CRF),
        "-preset", "medium",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(tmp),
    ]


def concat_clips(
    paths: list[Path], dst: Path, on_progress: ProgressFn | None = None
) -> str:
    """Concatenate clips sharing the locked profile into `dst`.

    Returns "copy" if `-c copy` produced a correct file, "reencode" if the
    inputs could not safely be copied or if the copy came out wrong.

    `-c copy` is the whole reason clips are cut at one locked profile: it
    remuxes without touching a pixel, so a twenty-minute reel takes about a
    second. Two guards stand around it, because ffmpeg provides neither:

    1. BEFORE: the inputs are compared to each other (see ClipParams). A
       divergence skips the copy entirely -- on ffmpeg 9.0.1 the copy would
       SUCCEED and yield a wrong-shaped or audio-truncated reel with a
       perfectly correct duration.
    2. AFTER: the output's duration is measured against the sum of the
       inputs, because a copy can also drop later inputs outright. That one
       IS visible in the duration, and this is the check that sees it.

    `on_progress` is threaded to the re-encode only. The copy is effectively
    instantaneous; the fallback is minutes on a real reel, and it is the one
    worth a badge.
    """
    if not paths:
        raise ConcatError("a reel needs at least one clip to concatenate")
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise ConcatError(f"{len(missing)} clip(s) missing, first: {missing[0]}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    expected_ms = sum(probe(p).duration_ms for p in paths)

    # Both temps are dot-prefixed siblings of dst: same directory, hence same
    # filesystem, so the os.replace() below is atomic and can never straddle
    # a mount -- and a plain listing of reels/ does not turn them up. Same
    # discipline make_clip uses for a clip in flight.
    stamp = uuid.uuid4().hex
    listing = dst.with_name(f".{dst.stem}.{stamp}.concat.txt")
    tmp = dst.with_name(f".{dst.stem}.{stamp}.part{dst.suffix}")

    try:
        listing.write_text("".join(f"file '{_escape(p)}'\n" for p in paths))
        # -safe 0 because the listing carries absolute paths, which the
        # demuxer refuses by default.
        base = ["-f", "concat", "-safe", "0", "-i", str(listing)]

        differences = divergences(paths)
        if differences:
            for difference in differences:
                log.warning("reel input mismatch -- %s", difference)
            log.warning(
                "%d input mismatch(es) for %s; re-encoding rather than copying",
                len(differences), dst.name,
            )
            mode = "reencode"
        else:
            run_ffmpeg([*base, "-c", "copy", "-movflags", "+faststart", str(tmp)])
            actual_ms = probe(tmp).duration_ms
            mode = "copy"
            if abs(actual_ms - expected_ms) > tolerance_ms(len(paths)):
                log.warning(
                    "concat -c copy produced %dms from %d clips totalling %dms; "
                    "re-encoding %s",
                    actual_ms, len(paths), expected_ms, dst.name,
                )
                mode = "reencode"

        if mode == "reencode":
            run_ffmpeg(
                _reencode_args(base, tmp),
                on_progress=on_progress, total_ms=expected_ms,
            )

        os.replace(tmp, dst)
        return mode
    except BaseException:
        # Best-effort only, and explicitly suppressed: a permissions failure
        # cleaning up must never replace the TranscodeError already
        # propagating with an unrelated error about a temp file.
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    finally:
        with contextlib.suppress(OSError):
            listing.unlink(missing_ok=True)


# run_ffmpeg and TranscodeError are re-exported deliberately: tests
# monkeypatch `concat.run_ffmpeg`, which must be the name this module
# actually calls rather than the one in transcode.
__all__ = [
    "ClipParams",
    "ConcatError",
    "TranscodeError",
    "clip_params",
    "concat_clips",
    "divergences",
    "run_ffmpeg",
    "tolerance_ms",
]
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_concat.py tests/test_probe.py -q
```

Expected: PASS. `test_probe.py` passing untouched is the evidence Step 1 was a pure extraction.

- [ ] **Step 6: Full suite and lint**

```bash
~/miniconda3/envs/splitstep/bin/pytest -q && ~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add splitstep/media/concat.py splitstep/media/probe.py tests/test_concat.py
git commit -m "feat(reels): concat clips with -c copy, guarded on both sides"
```

---

### Task 3: Resolving a reel's items to clip status and a cut plan

**Files:**
- Create: `splitstep/reels.py`
- Modify: `splitstep/db/jobs.py` (add `has_pending_reel`)
- Modify: `splitstep/jobs/handlers.py` (`handle_clip` tolerates a payload with no `rally_id`)
- Test: `tests/test_reels.py` (create), `tests/test_handler_clip.py` (append one test)

**Interfaces:**
- Consumes: `splitstep.export.ExportPlan`, `splitstep.media.clips.clip_relpath`, `splitstep.db.reels.list_items`, `splitstep.db.jobs.has_pending_clip`.
- Produces:
  - `ReelItem` frozen dataclass with fields `source_id, session_id, source_idx, start_ms, end_ms, position, clip_relpath, clip_ready, rally`
  - `resolve_items(library, conn, reel_id) -> list[ReelItem]`
  - `missing_clip_count(items: list[ReelItem]) -> int`
  - `clip_paths(library, items: list[ReelItem]) -> list[Path]`
  - `plan_reel_export(library, conn, reel_id) -> ExportPlan`
  - `has_pending_reel(conn, reel_id: str) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reels.py`:

```python
from splitstep.db.jobs import enqueue, has_pending_reel
from splitstep.db.rallies import replace_rallies
from splitstep.db.reels import add_items, create_reel
from splitstep.db.sessions import add_source, find_or_create_session_for_date
from splitstep.detect.segment import Interval
from splitstep.media.clips import clip_relpath
from splitstep.reels import (
    clip_paths,
    missing_clip_count,
    plan_reel_export,
    resolve_items,
)

import pytest


@pytest.fixture
def seeded(conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.6)])
    return {"session_id": session_id, "source_id": source_id, "idx": idx}


def _cut(library, session_id, idx, start_ms, end_ms, name=None):
    clips = library.clips_dir(session_id)
    clips.mkdir(parents=True, exist_ok=True)
    path = clips / (name or clip_relpath(idx, start_ms, end_ms))
    path.write_bytes(b"fake clip")
    return path


def test_resolve_items_reports_position_source_and_span(library, conn, seeded):
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [
        (seeded["source_id"], 9000, 14000),
        (seeded["source_id"], 1000, 5000),
    ])

    items = resolve_items(library, conn, reel["id"])

    assert [(i.start_ms, i.end_ms) for i in items] == [(9000, 14000), (1000, 5000)]
    assert [i.position for i in items] == [0, 1]
    # session_id and source_idx are what the preview needs to build a proxy
    # URL, and a reel is session-agnostic -- the item cannot answer it alone.
    assert all(i.session_id == seeded["session_id"] for i in items)
    assert all(i.source_idx == seeded["idx"] for i in items)


def test_clip_ready_is_exact_path_existence(library, conn, seeded):
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [
        (seeded["source_id"], 1000, 5000),
        (seeded["source_id"], 9000, 14000),
    ])
    _cut(library, seeded["session_id"], seeded["idx"], 1000, 5000)

    items = resolve_items(library, conn, reel["id"])

    assert [i.clip_ready for i in items] == [True, False]
    assert missing_clip_count(items) == 1


def test_a_part_file_is_not_a_clip(library, conn, seeded):
    # make_clip writes a dot-prefixed .part sibling and os.replace()s it onto
    # the real name only on success. Readiness is EXACT-PATH existence, never
    # a glob over clips/, so a live encode's output cannot be counted as a
    # finished clip. This test is what keeps a future refactor from
    # introducing the glob find_orphan_clips already had to avoid.
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 5000)])
    _cut(library, seeded["session_id"], seeded["idx"], 1000, 5000,
         name=f".{clip_relpath(seeded['idx'], 1000, 5000)[:-4]}.deadbeef.part.mp4")

    items = resolve_items(library, conn, reel["id"])
    assert items[0].clip_ready is False


def test_an_item_resolves_its_rally(library, conn, seeded):
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 5000)])
    rally = resolve_items(library, conn, reel["id"])[0].rally
    assert rally is not None
    assert rally["idx"] == 1
    assert rally["confidence"] == pytest.approx(0.8)


def test_an_orphaned_item_survives_a_resegment(library, conn, seeded):
    # §5: an item whose rally has vanished renders as orphaned but is still
    # playable and still renderable, never silently dropped. The clip on disk
    # is what the reel is made of, and it still exists.
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 5000)])
    _cut(library, seeded["session_id"], seeded["idx"], 1000, 5000)
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1200, 4800, 0.7)])

    items = resolve_items(library, conn, reel["id"])

    assert len(items) == 1
    assert items[0].rally is None
    assert items[0].clip_ready is True


def test_rally_lookup_is_exact_span_not_overlap(library, conn, seeded):
    # Deliberately not the >50% overlap rule replace_rallies uses to carry
    # flags. An item IS a clip, and the clip is named for its exact bounds --
    # a rally at (1200, 4800) is a different cut from (1000, 5000), so
    # showing its metadata here would mislabel the row.
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1100, 4900)])
    assert resolve_items(library, conn, reel["id"])[0].rally is None


def test_clip_paths_are_in_reel_order(library, conn, seeded):
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [
        (seeded["source_id"], 9000, 14000),
        (seeded["source_id"], 1000, 5000),
    ])
    items = resolve_items(library, conn, reel["id"])
    assert [p.name for p in clip_paths(library, items)] == [
        clip_relpath(seeded["idx"], 9000, 14000),
        clip_relpath(seeded["idx"], 1000, 5000),
    ]


def test_plan_reel_export_queues_only_missing_spans(library, conn, seeded):
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [
        (seeded["source_id"], 1000, 5000),
        (seeded["source_id"], 9000, 14000),
    ])
    _cut(library, seeded["session_id"], seeded["idx"], 1000, 5000)

    plan = plan_reel_export(library, conn, reel["id"])

    assert plan.already_cut == 1
    assert [(p["start_ms"], p["end_ms"]) for p in plan.pending] == [(9000, 14000)]
    assert plan.total == 2


def test_plan_reel_export_reports_in_flight_separately(library, conn, seeded):
    # The four outcomes stay four. Collapsing them is what once made a second
    # press mid-encode report everything as done.
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 5000)])
    enqueue(conn, "clip", {
        "source_id": seeded["source_id"], "start_ms": 1000, "end_ms": 5000,
    })

    plan = plan_reel_export(library, conn, reel["id"])

    assert (plan.in_flight, plan.already_cut, len(plan.pending)) == (1, 0, 0)


def test_plan_reel_export_carries_rally_id_when_there_is_one(library, conn, seeded):
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 5000)])
    payload = plan_reel_export(library, conn, reel["id"]).pending[0]
    assert payload["rally_id"] is not None


def test_plan_reel_export_omits_rally_id_for_an_orphan(library, conn, seeded):
    # An orphan must stay cuttable: cutting needs a source and a span, and
    # nothing else. Refusing here would leave a reel permanently unrenderable
    # with no way for the user to fix it.
    reel = create_reel(conn, "r")
    add_items(conn, reel["id"], [(seeded["source_id"], 1000, 5000)])
    replace_rallies(conn, seeded["session_id"], seeded["source_id"], [])

    payload = plan_reel_export(library, conn, reel["id"]).pending[0]

    assert "rally_id" not in payload
    assert (payload["source_id"], payload["start_ms"], payload["end_ms"]) == (
        seeded["source_id"], 1000, 5000,
    )


def test_has_pending_reel(conn):
    reel = create_reel(conn, "r")
    other = create_reel(conn, "other")
    assert has_pending_reel(conn, reel["id"]) is False
    enqueue(conn, "reel", {"reel_id": reel["id"]})
    assert has_pending_reel(conn, reel["id"]) is True
    # Matched via json_extract, so one reel's render can never suppress
    # another's -- the same reason has_pending_clip exists beside
    # has_pending_job.
    assert has_pending_reel(conn, other["id"]) is False
```

Append to `tests/test_handler_clip.py`:

```python
def test_handle_clip_without_a_rally_id_still_cuts(library, conn, a_rally):
    # A reel item whose rally vanished under a re-segment has no rally_id to
    # offer, and it must still be cuttable -- see plan_reel_export. The clip
    # is written; there is simply no row to record clip_path on.
    payload = {k: v for k, v in a_rally["payload"].items() if k != "rally_id"}
    handle_clip(library, payload)
    assert _clip_path(library, a_rally["source"], 200, 1200).exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_reels.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'splitstep.reels'`.

- [ ] **Step 3: Add `has_pending_reel` to `splitstep/db/jobs.py`**

Place it directly after `has_pending_clip`:

```python
def has_pending_reel(conn: sqlite3.Connection, reel_id: str) -> bool:
    """True if a render for exactly this reel is already queued or running.

    Its own function rather than a `has_pending_job` call because that one
    matches on `$.source_id`, which a reel payload does not carry -- a reel
    is session-agnostic and can span sources. Guards the render route against
    a double-click queuing two concatenations of the same reel onto the same
    output path.
    """
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE type = 'reel' AND status IN ('queued', 'running')"
        " AND json_extract(payload, '$.reel_id') = ? LIMIT 1",
        (reel_id,),
    ).fetchone()
    return row is not None
```

- [ ] **Step 4: Make `rally_id` optional in `handle_clip`**

In `splitstep/jobs/handlers.py`, replace the last line of `handle_clip`:

```python
    make_clip(src, dst, start_ms=start_ms, end_ms=end_ms,
              rotation_deg=source["rotation_deg"])

    # A reel item whose rally vanished under a re-segment carries no
    # rally_id (see plan_reel_export), and it must still be cuttable: the
    # cut needs a source and a span and nothing else. clip_path is a
    # convenience recorded on a rally when there is one -- the clip on disk
    # is the real artifact, and it is named for its span either way.
    rally_id = payload.get("rally_id")
    if rally_id is not None:
        set_clip_path(conn, rally_id, str(dst.relative_to(library.root)))
```

- [ ] **Step 5: Write `splitstep/reels.py`**

```python
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from splitstep.config import Library
from splitstep.db.jobs import has_pending_clip
from splitstep.db.reels import list_items
from splitstep.export import ExportPlan
from splitstep.media.clips import clip_relpath


@dataclass(frozen=True)
class ReelItem:
    """One reel row, resolved against the library and the rally table.

    `session_id` and `source_idx` are joined in rather than stored: a reel is
    session-agnostic by design (§5), so the item alone cannot say where its
    footage lives, and both the clip path and the preview's proxy URL need
    it. The join is safe without a LEFT: reel_items cascades on source_id, so
    an item whose source is gone is gone too.

    `rally` is None for an ORPHAN -- an item whose span no rally holds any
    more, which a threshold sweep produces routinely. It renders as orphaned
    and stays playable, cuttable and renderable: the clip on disk is what the
    reel is made of, and a rally is a guess the detector re-makes every sweep.
    """

    source_id: str
    session_id: str
    source_idx: int
    start_ms: int
    end_ms: int
    position: int
    clip_relpath: str
    clip_ready: bool
    rally: dict | None

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def resolve_items(
    library: Library, conn: sqlite3.Connection, reel_id: str
) -> list[ReelItem]:
    """Every item of a reel, in order, with its clip status and rally.

    Readiness is the existence of ONE exact path, never a listing of
    `clips/`. That is not an optimization: `make_clip` writes a dot-prefixed
    `.part` sibling while an encode is in flight, so a glob would count a
    live (or dead) temp file as a finished clip. Asking for the exact name
    the span implies cannot make that mistake -- and it is the same question
    plan_export asks, so the builder and the exporter can never disagree
    about what "cut" means.
    """
    rows = list_items(conn, reel_id)
    if not rows:
        return []

    sources = {
        r["id"]: r
        for r in conn.execute(
            "SELECT id, session_id, idx FROM sources WHERE id IN"
            f" ({','.join('?' * len({r['source_id'] for r in rows}))})",
            tuple({r["source_id"] for r in rows}),
        )
    }

    items: list[ReelItem] = []
    for row in rows:
        source = sources[row["source_id"]]
        name = clip_relpath(source["idx"], row["start_ms"], row["end_ms"])
        # Exact span, deliberately not the >50% overlap rule replace_rallies
        # uses to carry flags across a sweep. An item IS a clip, and the clip
        # is named for these exact bounds -- showing a neighbouring rally's
        # duration and confidence here would mislabel the row.
        rally = conn.execute(
            "SELECT * FROM rallies WHERE source_id = ? AND start_ms = ? AND end_ms = ?",
            (row["source_id"], row["start_ms"], row["end_ms"]),
        ).fetchone()
        items.append(ReelItem(
            source_id=row["source_id"],
            session_id=source["session_id"],
            source_idx=source["idx"],
            start_ms=row["start_ms"],
            end_ms=row["end_ms"],
            position=row["position"],
            clip_relpath=name,
            clip_ready=(library.clips_dir(source["session_id"]) / name).exists(),
            rally=dict(rally) if rally is not None else None,
        ))
    return items


def missing_clip_count(items: list[ReelItem]) -> int:
    return sum(1 for i in items if not i.clip_ready)


def clip_paths(library: Library, items: list[ReelItem]) -> list[Path]:
    """Absolute clip paths in reel order -- the concat demuxer's input list."""
    return [library.clips_dir(i.session_id) / i.clip_relpath for i in items]


def plan_reel_export(
    library: Library, conn: sqlite3.Connection, reel_id: str
) -> ExportPlan:
    """Sort a reel's items into spans needing a cut, plus why the rest do not.

    Reuses Plan A's ExportPlan rather than reimplementing the decision, and
    keeps its four outcomes four: collapsing `queued` / `already_cut` /
    `in_flight` into one bucket is what once made a second press mid-encode
    report everything as done.

    `unavailable` is structurally always 0 here and the field is kept anyway,
    so the reel and session endpoints return one shape. A session export can
    hit a rally whose source row is gone; a reel item cannot, because
    reel_items cascades on source_id.
    """
    pending: list[dict] = []
    already_cut = 0
    in_flight = 0

    for item in resolve_items(library, conn, reel_id):
        if has_pending_clip(conn, item.source_id, item.start_ms, item.end_ms):
            # Checked BEFORE the on-disk check, exactly as plan_export does:
            # a killed-and-requeued job can leave a stale complete file at
            # this path from an earlier run, and reading that as "already
            # cut" while a live job is about to overwrite it is wrong.
            in_flight += 1
            continue
        if item.clip_ready:
            already_cut += 1
            continue
        payload = {
            "source_id": item.source_id,
            "start_ms": item.start_ms,
            "end_ms": item.end_ms,
        }
        # Only when there is a rally to record it on -- handle_clip treats
        # the key as optional precisely so an orphan stays cuttable.
        if item.rally is not None:
            payload["rally_id"] = item.rally["id"]
        pending.append(payload)

    return ExportPlan(pending=pending, already_cut=already_cut, in_flight=in_flight)
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_reels.py tests/test_handler_clip.py -q
```

Expected: PASS.

- [ ] **Step 7: Full suite and lint**

```bash
~/miniconda3/envs/splitstep/bin/pytest -q && ~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add splitstep/reels.py splitstep/db/jobs.py splitstep/jobs/handlers.py tests/test_reels.py tests/test_handler_clip.py
git commit -m "feat(reels): resolve reel items to clip status and a cut plan"
```

---

### Task 4: The `reel` job

**Files:**
- Modify: `splitstep/jobs/handlers.py` (add `handle_reel`, register it in `HANDLERS`)
- Test: `tests/test_handler_reel.py` (create)

**Interfaces:**
- Consumes: `splitstep.reels.resolve_items`, `splitstep.reels.clip_paths`, `splitstep.reels.missing_clip_count`, `splitstep.media.concat.concat_clips`, `splitstep.db.reels.get_reel`, `splitstep.db.reels.mark_rendered`.
- Produces: `handle_reel(library, payload: dict, progress: ProgressFn = no_progress) -> None` — payload is `{"reel_id": str}`; `HANDLERS["reel"]`.

**The handler signature is three arguments now.** `Handler = Callable[[Library, dict, ProgressFn], None]` (`splitstep/jobs/worker.py`); every handler takes a third `progress` arg defaulting to `no_progress`, and the CLI and tests call handlers directly with two. A two-argument `handle_reel` will not satisfy the type and will break when the Worker calls it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_handler_reel.py`:

```python
import subprocess

import pytest

from splitstep.config import NotEnoughSpace
from splitstep.db.rallies import replace_rallies
from splitstep.db.reels import add_items, create_reel, get_reel
from splitstep.db.sessions import add_source, find_or_create_session_for_date
from splitstep.detect.segment import Interval
from splitstep.jobs.handlers import HANDLERS, handle_reel
from splitstep.media.clips import clip_relpath
from splitstep.media.probe import probe


def _real_clip(path, seconds=1.0):
    """A clip that shares one profile with its siblings, so -c copy applies.

    320x240 rather than the locked 3840x2160: this exercises the handler's
    wiring, and three 4K encodes per test would make the suite unusable. The
    profile parameters that -c copy actually cares about (codec, pix_fmt,
    rate, audio layout) still match across the inputs, which is the real
    precondition.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
         "-r", "30", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
         "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


@pytest.fixture
def reel_of_two(library, conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.6)])
    reel = create_reel(conn, "2026-08-18 points")
    spans = [(source_id, 1000, 5000), (source_id, 9000, 14000)]
    add_items(conn, reel["id"], spans)
    return {
        "reel": reel, "session_id": session_id, "source_id": source_id,
        "idx": idx, "spans": spans,
    }


def _cut_all(library, fx, seconds=1.0):
    clips = library.clips_dir(fx["session_id"])
    return [
        _real_clip(clips / clip_relpath(fx["idx"], start, end), seconds)
        for _src, start, end in fx["spans"]
    ]


def test_reel_is_registered(library):
    assert HANDLERS["reel"] is handle_reel


def test_handle_reel_writes_the_slug_named_file(library, conn, reel_of_two):
    parts = _cut_all(library, reel_of_two)

    handle_reel(library, {"reel_id": reel_of_two["reel"]["id"]})

    dst = library.reels_dir / "2026-08-18-points.mp4"
    assert dst.exists()
    total = sum(probe(p).duration_ms for p in parts)
    assert abs(probe(dst).duration_ms - total) <= 500


def test_handle_reel_records_a_library_relative_path_and_clears_dirty(
    library, conn, reel_of_two
):
    _cut_all(library, reel_of_two)
    handle_reel(library, {"reel_id": reel_of_two["reel"]["id"]})

    row = get_reel(conn, reel_of_two["reel"]["id"])
    # Library-relative, never absolute: the drive mounts at a different point
    # on each machine, same rule rallies.clip_path follows.
    assert row["rendered_path"] == "reels/2026-08-18-points.mp4"
    assert not row["rendered_path"].startswith("/")
    assert (library.root / row["rendered_path"]).exists()
    assert row["dirty"] == 0
    assert row["rendered_at"] is not None


def test_handle_reel_refuses_while_a_clip_is_missing(library, conn, reel_of_two):
    # Defence in depth behind the route's own 409: a clip can be deleted
    # between enqueue and run, and a reel silently short one point is exactly
    # the failure the duration probe exists to catch. Naming the count is
    # what makes the jobs badge's error readable.
    fx = reel_of_two
    clips = library.clips_dir(fx["session_id"])
    _real_clip(clips / clip_relpath(fx["idx"], 1000, 5000))

    with pytest.raises(ValueError, match="1 clip"):
        handle_reel(library, {"reel_id": fx["reel"]["id"]})

    assert not (library.reels_dir / "2026-08-18-points.mp4").exists()
    assert get_reel(conn, fx["reel"]["id"])["dirty"] == 1


def test_handle_reel_refuses_an_empty_reel(library, conn):
    reel = create_reel(conn, "empty")
    with pytest.raises(ValueError, match="no items"):
        handle_reel(library, {"reel_id": reel["id"]})


def test_handle_reel_refuses_an_unknown_reel(library):
    with pytest.raises(ValueError, match="No such reel"):
        handle_reel(library, {"reel_id": "nope"})


def test_handle_reel_is_idempotent(library, conn, reel_of_two):
    _cut_all(library, reel_of_two)
    handle_reel(library, {"reel_id": reel_of_two["reel"]["id"]})
    first = (library.reels_dir / "2026-08-18-points.mp4").stat().st_size
    handle_reel(library, {"reel_id": reel_of_two["reel"]["id"]})
    assert (library.reels_dir / "2026-08-18-points.mp4").stat().st_size == first


def test_handle_reel_checks_free_space_first(library, conn, reel_of_two, monkeypatch):
    _cut_all(library, reel_of_two)
    monkeypatch.setattr(type(library), "free_bytes", lambda self: 1)
    with pytest.raises(NotEnoughSpace):
        handle_reel(library, {"reel_id": reel_of_two["reel"]["id"]})
    assert not (library.reels_dir / "2026-08-18-points.mp4").exists()


def test_a_reencode_fallback_still_marks_the_reel_rendered(
    library, conn, reel_of_two, monkeypatch, caplog
):
    # The fallback is a slower success, not a failure: the reel is rendered
    # and dirty is cleared. It is logged so a silent -c copy problem leaves a
    # trace rather than only a slower render nobody notices.
    import splitstep.media.concat as concat_mod

    _cut_all(library, reel_of_two)
    real_run = concat_mod.run_ffmpeg
    seen = []

    def fake_run(args, timeout=None):
        seen.append(args)
        if "copy" in args and len(seen) == 1:
            real_run(["-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=0.2",
                      "-f", "lavfi", "-i", "sine=frequency=440:duration=0.2",
                      "-c:v", "libx264", "-pix_fmt", "yuv420p",
                      "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", args[-1]])
            return
        real_run(args)

    monkeypatch.setattr(concat_mod, "run_ffmpeg", fake_run)

    with caplog.at_level("WARNING"):
        handle_reel(library, {"reel_id": reel_of_two["reel"]["id"]})

    assert get_reel(conn, reel_of_two["reel"]["id"])["dirty"] == 0
    assert any("re-encod" in r.message or "re-encod" in r.getMessage()
               for r in caplog.records)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_handler_reel.py -q
```

Expected: FAIL — `ImportError: cannot import name 'handle_reel'`.

- [ ] **Step 3: Add the imports to `splitstep/jobs/handlers.py`**

Add to the existing import block (ruff `I001` sorts these — run the linter after):

```python
from splitstep.db.reels import get_reel, mark_rendered
from splitstep.media.concat import concat_clips
from splitstep.reels import clip_paths, missing_clip_count, resolve_items

`ProgressFn` and `no_progress` are already imported at the top of `handlers.py` (`from splitstep.jobs.worker import Handler, no_progress`, `from splitstep.media.transcode import ... ProgressFn ...`) — check before adding a duplicate line.
```

- [ ] **Step 4: Write `handle_reel` immediately after `handle_clip`**

```python
def handle_reel(library: Library, payload: dict,
                progress: ProgressFn = no_progress) -> None:
    """Concatenate a reel's clips into `reels/<slug>.mp4`.

    Idempotent by overwrite, like every other handler.

    Refuses while any clip is missing rather than cutting them itself. That
    is the same rule the render route enforces, repeated here rather than
    trusted: clips can be deleted between enqueue and run, and a reel is not
    a place to discover that four points are gone. Auto-enqueueing the cuts
    from inside a render would also turn one button into half an hour of
    encoding nobody asked for.
    """
    conn = _open(library)
    reel = get_reel(conn, payload["reel_id"])
    if reel is None:
        raise ValueError(f"No such reel: {payload['reel_id']}")

    items = resolve_items(library, conn, reel["id"])
    if not items:
        raise ValueError(f"Reel {reel['slug']} has no items to render")
    missing = missing_clip_count(items)
    if missing:
        raise ValueError(
            f"Reel {reel['slug']} has {missing} clip(s) not cut yet; "
            f"cut them before rendering"
        )

    inputs = clip_paths(library, items)
    dst = library.reels_dir / f"{reel['slug']}.mp4"

    # A -c copy remux is about the sum of its inputs. The re-encode fallback
    # can land either side of that, so double it -- and refusing early beats
    # dying at 90% of a twenty-minute reel, which is the whole point of the
    # check.
    library.require_free(sum(p.stat().st_size for p in inputs) * 2)

    # progress is threaded through to the re-encode fallback only -- the
    # copy is effectively instantaneous, and `activeJobsLabel` suppresses the
    # percentage entirely until a job reports one, so a copy simply shows the
    # job count. The fallback is minutes on a real reel and is worth a bar.
    mode = concat_clips(inputs, dst, on_progress=progress)
    if mode == "reencode":
        # concat_clips already logged the mismatch that caused this; this
        # line is what ties it to a reel by name in the same log.
        log.warning("reel %s fell back to a re-encode", reel["slug"])

    mark_rendered(conn, reel["id"], str(dst.relative_to(library.root)))
```

- [ ] **Step 5: Register it**

```python
HANDLERS: dict[str, Handler] = {
    "ingest": handle_ingest,
    "build_proxy": handle_build_proxy,
    "detect": handle_detect,
    "clip": handle_clip,
    "reel": handle_reel,
}
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_handler_reel.py -q
```

Expected: PASS, 9 tests.

- [ ] **Step 7: Full suite and lint**

```bash
~/miniconda3/envs/splitstep/bin/pytest -q && ~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
```

Expected: all green. If ruff flags `I001`, run `~/miniconda3/envs/splitstep/bin/ruff check --fix splitstep` and re-read the diff.

- [ ] **Step 8: Commit**

```bash
git add splitstep/jobs/handlers.py tests/test_handler_reel.py
git commit -m "feat(reels): add the reel job"
```

---

### Task 5: The reel API

**Files:**
- Modify: `splitstep/api/routes.py`
- Test: `tests/test_api_reels.py` (create)

**Interfaces:**
- Consumes: everything from Tasks 1, 3 and 4; `splitstep.export.column_for`, `splitstep.export.SETS`.
- Produces nine routes:

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/reels` | — | `[{...reel, item_count}]` |
| POST | `/api/reels` | `{name}` | `{...reel, item_count: 0}` |
| GET | `/api/reels/{slug}` | — | `{reel, items}` |
| POST | `/api/reels/{slug}/items` | `{items: [{source_id,start_ms,end_ms}]}` | `{added, existing, total}` |
| POST | `/api/reels/{slug}/items/remove` | `{source_id,start_ms,end_ms}` | `{removed, total}` |
| POST | `/api/reels/{slug}/order` | `{order: [{source_id,start_ms,end_ms}]}` | `{ok: true}` |
| POST | `/api/reels/{slug}/export` | — | `{queued, already_cut, in_flight, unavailable, total}` |
| POST | `/api/reels/{slug}/render` | — | `{job_id}` or 409 |
| POST | `/api/sessions/{session_id}/reels` | `{which}` | `{slug, name, added, existing, total}` |

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_reels.py`:

```python
import pytest
from fastapi.testclient import TestClient

from splitstep.api.app import create_app
from splitstep.db.rallies import replace_rallies, set_point, set_star
from splitstep.db.reels import create_reel, get_reel_by_slug
from splitstep.db.sessions import add_source, find_or_create_session_for_date
from splitstep.detect.segment import Interval
from splitstep.media.clips import clip_relpath


@pytest.fixture
def client(library):
    with TestClient(create_app(library)) as c:
        yield c


@pytest.fixture
def session(conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-18")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-18T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_9000.MOV",
    )
    replace_rallies(conn, session_id, source_id, [
        Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.6), Interval(20000, 24000, 0.7),
    ])
    rallies = conn.execute("SELECT * FROM rallies ORDER BY idx").fetchall()
    set_point(conn, rallies[0]["id"], True)
    set_point(conn, rallies[1]["id"], True)
    set_star(conn, rallies[1]["id"], True)
    return {
        "id": session_id, "source_id": source_id, "idx": idx,
        "rallies": [dict(r) for r in rallies],
    }


def _span(session, start, end):
    return {"source_id": session["source_id"], "start_ms": start, "end_ms": end}


def test_create_and_list_a_reel(client):
    created = client.post("/api/reels", json={"name": "Best of July"}).json()
    assert created["slug"] == "best-of-july"
    assert created["item_count"] == 0

    listed = client.get("/api/reels").json()
    assert [r["slug"] for r in listed] == ["best-of-july"]


def test_creating_a_reel_with_a_blank_name_is_refused(client):
    assert client.post("/api/reels", json={"name": "   "}).status_code == 422


def test_get_a_reel_resolves_its_items(client, conn, session, library):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    client.post(f"/api/reels/{reel['slug']}/items",
                json={"items": [_span(session, 1000, 5000)]})

    body = client.get(f"/api/reels/{reel['slug']}").json()

    assert body["reel"]["slug"] == reel["slug"]
    # Same shape as a listed reel: the frontend shares one type across
    # /api/reels and this route, so a missing item_count would read as
    # undefined at runtime while the type promised a number.
    assert body["reel"]["item_count"] == 1
    item = body["items"][0]
    # Everything the builder row and the preview need, in one response.
    assert item["session_id"] == session["id"]
    assert item["source_idx"] == session["idx"]
    assert item["duration_ms"] == 4000
    assert item["clip_ready"] is False
    assert item["rally"]["idx"] == 1


def test_get_an_unknown_reel_is_404(client):
    assert client.get("/api/reels/nope").status_code == 404


def test_adding_items_is_additive(client, session):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    slug = reel["slug"]
    client.post(f"/api/reels/{slug}/items", json={"items": [_span(session, 1000, 5000)]})

    body = client.post(f"/api/reels/{slug}/items", json={"items": [
        _span(session, 1000, 5000), _span(session, 9000, 14000),
    ]}).json()

    assert body == {"added": 1, "existing": 1, "total": 2}


def test_adding_an_item_marks_the_reel_dirty(client, conn, session):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    conn.execute("UPDATE reels SET dirty = 0 WHERE slug = ?", (reel["slug"],))
    conn.commit()

    client.post(f"/api/reels/{reel['slug']}/items",
                json={"items": [_span(session, 1000, 5000)]})

    assert client.get(f"/api/reels/{reel['slug']}").json()["reel"]["dirty"] == 1


def test_removing_an_item(client, session):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    slug = reel["slug"]
    client.post(f"/api/reels/{slug}/items", json={"items": [
        _span(session, 1000, 5000), _span(session, 9000, 14000),
    ]})

    body = client.post(f"/api/reels/{slug}/items/remove",
                       json=_span(session, 1000, 5000)).json()

    assert body == {"removed": True, "total": 1}
    assert client.post(f"/api/reels/{slug}/items/remove",
                       json=_span(session, 1000, 5000)).json()["removed"] is False


def test_reordering(client, session):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    slug = reel["slug"]
    client.post(f"/api/reels/{slug}/items", json={"items": [
        _span(session, 1000, 5000), _span(session, 9000, 14000),
    ]})

    res = client.post(f"/api/reels/{slug}/order", json={"order": [
        _span(session, 9000, 14000), _span(session, 1000, 5000),
    ]})

    assert res.status_code == 200
    items = client.get(f"/api/reels/{slug}").json()["items"]
    assert [i["start_ms"] for i in items] == [9000, 1000]


def test_a_reorder_that_is_not_the_membership_is_refused(client, session):
    # A stale client list racing a removal. 409, not 500: the client's view
    # is out of date, and refetching is the fix.
    reel = client.post("/api/reels", json={"name": "r"}).json()
    slug = reel["slug"]
    client.post(f"/api/reels/{slug}/items", json={"items": [
        _span(session, 1000, 5000), _span(session, 9000, 14000),
    ]})

    res = client.post(f"/api/reels/{slug}/order",
                      json={"order": [_span(session, 1000, 5000)]})

    assert res.status_code == 409


def test_reel_export_queues_clip_jobs(client, conn, session):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    client.post(f"/api/reels/{reel['slug']}/items", json={"items": [
        _span(session, 1000, 5000), _span(session, 9000, 14000),
    ]})

    body = client.post(f"/api/reels/{reel['slug']}/export").json()

    assert body == {"queued": 2, "already_cut": 0, "in_flight": 0,
                    "unavailable": 0, "total": 2}
    types = [r["type"] for r in conn.execute("SELECT type FROM jobs")]
    assert types == ["clip", "clip"]


def test_a_second_export_reports_in_flight_not_already_cut(client, session):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    client.post(f"/api/reels/{reel['slug']}/items",
                json={"items": [_span(session, 1000, 5000)]})
    client.post(f"/api/reels/{reel['slug']}/export")

    body = client.post(f"/api/reels/{reel['slug']}/export").json()

    assert body["queued"] == 0
    assert body["in_flight"] == 1
    assert body["already_cut"] == 0


def test_render_refuses_while_clips_are_missing(client, conn, session):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    client.post(f"/api/reels/{reel['slug']}/items", json={"items": [
        _span(session, 1000, 5000), _span(session, 9000, 14000),
    ]})

    res = client.post(f"/api/reels/{reel['slug']}/render")

    assert res.status_code == 409
    # The count is named, so the UI can say WHY without a second request.
    assert "2" in res.json()["detail"]
    assert conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 0


def test_render_enqueues_once_clips_exist(client, conn, session, library):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    client.post(f"/api/reels/{reel['slug']}/items",
                json={"items": [_span(session, 1000, 5000)]})
    clips = library.clips_dir(session["id"])
    clips.mkdir(parents=True, exist_ok=True)
    (clips / clip_relpath(session["idx"], 1000, 5000)).write_bytes(b"fake")

    body = client.post(f"/api/reels/{reel['slug']}/render").json()

    assert body["job_id"]
    row = conn.execute("SELECT type, payload FROM jobs").fetchone()
    assert row["type"] == "reel"


def test_render_refuses_an_empty_reel(client):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    assert client.post(f"/api/reels/{reel['slug']}/render").status_code == 409


def test_a_second_render_does_not_queue_twice(client, conn, session, library):
    reel = client.post("/api/reels", json={"name": "r"}).json()
    client.post(f"/api/reels/{reel['slug']}/items",
                json={"items": [_span(session, 1000, 5000)]})
    clips = library.clips_dir(session["id"])
    clips.mkdir(parents=True, exist_ok=True)
    (clips / clip_relpath(session["idx"], 1000, 5000)).write_bytes(b"fake")

    first = client.post(f"/api/reels/{reel['slug']}/render").json()
    second = client.post(f"/api/reels/{reel['slug']}/render").json()

    assert first["job_id"] == second["job_id"]
    assert conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 1


def test_session_reel_creates_from_the_point_set(client, session):
    body = client.post(f"/api/sessions/{session['id']}/reels",
                       json={"which": "points"}).json()

    assert body["name"] == "2026-08-18 points"
    assert body["slug"] == "2026-08-18-points"
    assert body["added"] == 2

    items = client.get("/api/reels/2026-08-18-points").json()["items"]
    # Chronological, which for one source is start order.
    assert [i["start_ms"] for i in items] == [1000, 9000]


def test_session_reel_creates_from_the_starred_set(client, session):
    body = client.post(f"/api/sessions/{session['id']}/reels",
                       json={"which": "starred"}).json()
    assert body["slug"] == "2026-08-18-starred"
    assert body["added"] == 1


def test_a_second_click_merges_into_the_same_reel(client, conn, session):
    first = client.post(f"/api/sessions/{session['id']}/reels",
                        json={"which": "points"}).json()
    slug = first["slug"]
    # Reorder by hand, then mark a third rally a point and click again.
    client.post(f"/api/reels/{slug}/order", json={"order": [
        _span(session, 9000, 14000), _span(session, 1000, 5000),
    ]})
    set_point(conn, session["rallies"][2]["id"], True)

    second = client.post(f"/api/sessions/{session['id']}/reels",
                         json={"which": "points"}).json()

    assert second["slug"] == slug
    assert (second["added"], second["existing"]) == (1, 2)
    items = client.get(f"/api/reels/{slug}").json()["items"]
    # The hand-ordering survives and the new span lands at the end. Anything
    # else silently discards a manual reorder.
    assert [i["start_ms"] for i in items] == [9000, 1000, 20000]


def test_a_session_reel_skips_rejected_rallies(client, conn, session):
    conn.execute("UPDATE rallies SET rejected = 1 WHERE start_ms = 1000")
    conn.commit()
    body = client.post(f"/api/sessions/{session['id']}/reels",
                       json={"which": "points"}).json()
    assert body["added"] == 1


def test_a_session_reel_with_a_bad_set_is_refused(client, session):
    res = client.post(f"/api/sessions/{session['id']}/reels", json={"which": "all"})
    assert res.status_code == 422


def test_a_session_reel_for_an_unknown_session_is_404(client):
    res = client.post("/api/sessions/nope/reels", json={"which": "points"})
    assert res.status_code == 404


def test_a_generated_slug_yields_to_one_already_taken(client, conn, session):
    # A hand-made reel took the slug first. The generated reel must not
    # collide, and must not silently merge into a reel it did not create --
    # it merges by NAME, and this one's name is different.
    create_reel(conn, "2026-08-18 Points")
    assert get_reel_by_slug(conn, "2026-08-18-points") is not None

    body = client.post(f"/api/sessions/{session['id']}/reels",
                       json={"which": "points"}).json()

    assert body["slug"] == "2026-08-18-points-2"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_api_reels.py -q
```

Expected: FAIL — 404s everywhere, and `ImportError` for `splitstep.db.reels` names if a typo crept in.

- [ ] **Step 3: Add imports to `splitstep/api/routes.py`**

```python
from splitstep.db.reels import (
    add_items,
    create_reel,
    find_reel_by_name,
    get_reel_by_slug,
    list_reels,
    remove_item,
    set_order,
)
from splitstep.export import SETS, column_for, plan_export
from splitstep.reels import missing_clip_count, plan_reel_export, resolve_items
```

(`from splitstep.export import SETS, plan_export` already exists — extend it rather than adding a second line, or ruff `I001` will complain.)

- [ ] **Step 4: Add the request bodies**

After `ExportBody`:

```python
class ReelCreateBody(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str) -> str:
        # Stripped here rather than at the call site so the slug and the
        # displayed name are derived from the same string -- a name of pure
        # whitespace would otherwise slug to "reel" and render as blank.
        name = v.strip()
        if not name:
            raise ValueError("a reel needs a name")
        return name


class SpanBody(BaseModel):
    """One reel item, addressed the way reel_items keys it.

    Never a rally_id: replace_rallies deletes every rally for a source on a
    sweep, so a client holding one has a reference that expires. A span does
    not -- it is what the clip on disk is named for.
    """

    source_id: str
    start_ms: int
    end_ms: int

    @model_validator(mode="after")
    def check_order(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self

    def as_tuple(self) -> tuple[str, int, int]:
        return (self.source_id, self.start_ms, self.end_ms)


class ReelItemsBody(BaseModel):
    items: list[SpanBody]


class ReelOrderBody(BaseModel):
    order: list[SpanBody]


class SessionReelBody(BaseModel):
    which: str

    @field_validator("which")
    @classmethod
    def check_which(cls, v: str) -> str:
        if v not in SETS:
            raise ValueError(f"which must be one of {list(SETS)}")
        return v
```

- [ ] **Step 5: Add the routes, at the end of `splitstep/api/routes.py`**

```python
def _reel_or_404(conn: sqlite3.Connection, slug: str) -> sqlite3.Row:
    reel = get_reel_by_slug(conn, slug)
    if reel is None:
        raise HTTPException(status_code=404, detail="Reel not found")
    return reel


def _item_json(item) -> dict:
    """One builder row. `rally` is None for an orphan -- an item whose span no
    rally holds any more, which the UI badges rather than hides."""
    return {
        "source_id": item.source_id,
        "session_id": item.session_id,
        "source_idx": item.source_idx,
        "start_ms": item.start_ms,
        "end_ms": item.end_ms,
        "duration_ms": item.duration_ms,
        "position": item.position,
        "clip_ready": item.clip_ready,
        "rally": item.rally,
    }


@router.get("/api/reels")
def api_list_reels(request: Request):
    return [dict(r) for r in list_reels(_conn(request))]


@router.post("/api/reels")
def api_create_reel(body: ReelCreateBody, request: Request):
    reel = create_reel(_conn(request), body.name)
    # item_count so a freshly created reel has the same shape as a listed
    # one; the list page renders straight from either.
    return {**dict(reel), "item_count": 0}


@router.get("/api/reels/{slug}")
def api_get_reel(slug: str, request: Request):
    conn = _conn(request)
    reel = _reel_or_404(conn, slug)
    items = resolve_items(_library(request), conn, reel["id"])
    return {
        # item_count so a single reel has the SAME shape as a listed one.
        # The frontend shares one `Reel` type across both routes, so a
        # missing field here would be `undefined` at runtime while the type
        # promised a number -- silent until something rendered it. Taken
        # from the already-resolved items rather than a second COUNT(*), so
        # the two can never disagree.
        "reel": {**dict(reel), "item_count": len(items)},
        "items": [_item_json(i) for i in items],
    }


@router.post("/api/reels/{slug}/items")
def api_add_reel_items(slug: str, body: ReelItemsBody, request: Request):
    conn = _conn(request)
    reel = _reel_or_404(conn, slug)
    spans = [s.as_tuple() for s in body.items]
    added = add_items(conn, reel["id"], spans)
    # `existing` is reported separately rather than folded into a single
    # "total added" so a second click can honestly say "1 added, 2 already
    # there" instead of implying it did nothing.
    return {
        "added": added,
        "existing": len(set(spans)) - added,
        "total": len(resolve_items(_library(request), conn, reel["id"])),
    }


@router.post("/api/reels/{slug}/items/remove")
def api_remove_reel_item(slug: str, body: SpanBody, request: Request):
    conn = _conn(request)
    reel = _reel_or_404(conn, slug)
    removed = remove_item(conn, reel["id"], body.source_id, body.start_ms, body.end_ms)
    return {
        "removed": removed,
        "total": len(resolve_items(_library(request), conn, reel["id"])),
    }


@router.post("/api/reels/{slug}/order")
def api_set_reel_order(slug: str, body: ReelOrderBody, request: Request):
    conn = _conn(request)
    reel = _reel_or_404(conn, slug)
    try:
        set_order(conn, reel["id"], [s.as_tuple() for s in body.order])
    except ValueError as exc:
        # 409, not 422: the request is well-formed, the client's view of the
        # membership is simply stale (a removal in another tab, most likely).
        # Refetching is the fix, and the UI says so.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/api/reels/{slug}/export")
def api_export_reel_clips(slug: str, request: Request):
    """Cut the clips this reel is missing. Never called by render."""
    conn = _conn(request)
    reel = _reel_or_404(conn, slug)
    plan = plan_reel_export(_library(request), conn, reel["id"])
    for payload in plan.pending:
        jobq.enqueue(conn, "clip", payload)
    return {
        "queued": len(plan.pending),
        "already_cut": plan.already_cut,
        "in_flight": plan.in_flight,
        "unavailable": plan.unavailable,
        "total": plan.total,
    }


@router.post("/api/reels/{slug}/render")
def api_render_reel(slug: str, request: Request):
    """Enqueue the concat. Refuses while any clip is missing, naming the count.

    Deliberately does NOT cut the missing clips: a button labelled "render"
    must not start half an hour of encoding. Cutting stays the separate,
    explicitly-pressed action next to it.
    """
    conn = _conn(request)
    reel = _reel_or_404(conn, slug)
    items = resolve_items(_library(request), conn, reel["id"])
    if not items:
        raise HTTPException(status_code=409, detail="This reel has no items yet.")
    missing = missing_clip_count(items)
    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"{missing} clip(s) not cut yet. Cut them first.",
        )

    # A double-click must not queue two concats onto one output path.
    # Returning the job already in flight makes the second press honest
    # rather than a silent no-op.
    existing = conn.execute(
        "SELECT id FROM jobs WHERE type = 'reel' AND status IN ('queued', 'running')"
        " AND json_extract(payload, '$.reel_id') = ? LIMIT 1",
        (reel["id"],),
    ).fetchone()
    if existing is not None:
        return {"job_id": existing["id"], "already_running": True}

    return {"job_id": jobq.enqueue(conn, "reel", {"reel_id": reel["id"]}),
            "already_running": False}


@router.post("/api/sessions/{session_id}/reels")
def api_session_reel(session_id: str, body: SessionReelBody, request: Request):
    """Create (or additively merge into) the reel for a session's point or
    starred set, and return its slug so the client can open the builder.

    Resolved by NAME, not by slug: the second click must land in the reel the
    first one made, and a hand-made reel that happens to slug the same is a
    different reel with a different name. When a name is new, unique_slug
    yields to whatever already holds the slug (see create_reel).

    Membership is added, never set. Overwriting would silently discard a
    manual reorder -- the same class of mistake replace_rallies makes with
    boundary edits, which already cost this project a 9.6-second rally.
    """
    conn = _conn(request)
    session = get_session(conn, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    column = column_for(body.which)
    rows = conn.execute(
        f"SELECT source_id, start_ms, end_ms FROM rallies WHERE session_id = ?"
        f" AND {column} = 1 AND rejected = 0 ORDER BY idx",
        (session_id,),
    ).fetchall()
    spans = [(r["source_id"], r["start_ms"], r["end_ms"]) for r in rows]

    name = f"{session['played_on']} {body.which}"
    reel = find_reel_by_name(conn, name) or create_reel(conn, name)
    added = add_items(conn, reel["id"], spans)

    return {
        "slug": reel["slug"],
        "name": reel["name"],
        "added": added,
        "existing": len(spans) - added,
        "total": len(resolve_items(_library(request), conn, reel["id"])),
    }
```

Note `column_for(body.which)` is called even though `SessionReelBody` already validated `which`: that is the second, independent gate `splitstep/export.py` documents, and the value is interpolated into the WHERE clause through an f-string. Do not remove either one.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_api_reels.py -q
```

Expected: PASS, 21 tests.

- [ ] **Step 7: Full suite and lint**

```bash
~/miniconda3/envs/splitstep/bin/pytest -q && ~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add splitstep/api/routes.py tests/test_api_reels.py
git commit -m "feat(reels): the reel API"
```

---

### Task 6: Routes, types, API client, and the `/reels` list

**Files:**
- Modify: `web/src/lib/router.svelte.ts`, `web/src/lib/types.ts`, `web/src/lib/api.ts`, `web/src/App.svelte`, `web/src/routes/Library.svelte`
- Create: `web/src/routes/Reels.svelte`
- Test: `web/tests/router.test.ts` (append)

**Interfaces:**
- Produces:
  - `Route` gains `{ name: 'reels' }` and `{ name: 'reel'; slug: string }`
  - `Reel`, `ReelItem`, `ReelDetail`, `ReelMergeResult`, `RenderResult` in `types.ts`
  - `api.listReels`, `api.createReel`, `api.getReel`, `api.addReelItems`, `api.removeReelItem`, `api.setReelOrder`, `api.exportReelClips`, `api.renderReel`, `api.createSessionReel`

- [ ] **Step 1: Write the failing router tests**

Append to `web/tests/router.test.ts`:

```ts
describe('parseHash - reel routes', () => {
  it('parses the reels list', () => {
    expect(parseHash('#/reels')).toEqual({ name: 'reels' })
    expect(parseHash('#/reels/')).toEqual({ name: 'reels' })
  })

  it('parses a reel builder by slug', () => {
    expect(parseHash('#/reels/2026-08-18-points')).toEqual({
      name: 'reel',
      slug: '2026-08-18-points',
    })
  })

  it('keeps a collision-suffixed slug intact', () => {
    expect(parseHash('#/reels/2026-08-18-points-2')).toEqual({
      name: 'reel',
      slug: '2026-08-18-points-2',
    })
  })

  it('falls back to the library for a deeper reel path', () => {
    expect(parseHash('#/reels/a/b')).toEqual({ name: 'library' })
  })
})
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd web && npx vitest run tests/router.test.ts
```

Expected: FAIL — `#/reels` resolves to `{ name: 'library' }`.

- [ ] **Step 3: Extend the router**

In `web/src/lib/router.svelte.ts`:

```ts
export type Route =
  | { name: 'library' }
  | { name: 'session'; id: string }
  | { name: 'setup'; id: string }
  | { name: 'reels' }
  | { name: 'reel'; slug: string }

export function parseHash(hash: string): Route {
  const path = hash.replace(/^#/, '')
  const parts = path.split('/').filter(Boolean)
  if (parts.length === 1 && parts[0] === 'reels') {
    return { name: 'reels' }
  }
  // The slug is one segment by construction -- slugify() collapses every
  // run of non-alphanumerics to a single hyphen, so a slug can never contain
  // a slash. A deeper path is therefore not a reel, and falls through.
  if (parts.length === 2 && parts[0] === 'reels') {
    return { name: 'reel', slug: parts[1] }
  }
  if (parts.length === 2 && parts[0] === 'setup') {
    return { name: 'setup', id: parts[1] }
  }
  if (parts.length === 2 && parts[0] === 's') {
    return { name: 'session', id: parts[1] }
  }
  return { name: 'library' }
}
```

- [ ] **Step 4: Run to verify they pass**

```bash
cd web && npx vitest run tests/router.test.ts
```

Expected: PASS.

- [ ] **Step 5: Add the types**

Append to `web/src/lib/types.ts`:

```ts
export interface Reel {
  id: string
  name: string
  slug: string
  rendered_path: string | null
  rendered_at: string | null
  dirty: number
  created_at: string
  item_count: number
}

/**
 * One row of the builder, as the server resolves it.
 *
 * `session_id` and `source_idx` are here because a reel is session-agnostic:
 * the item alone cannot say which proxy the preview should seek, and the
 * clip path needs the same pair. `rally` is null for an ORPHAN -- an item
 * whose span no rally holds any more, which a threshold sweep produces
 * routinely. It is badged, never dropped: the clip on disk is what the reel
 * is made of.
 */
export interface ReelItem {
  source_id: string
  session_id: string
  source_idx: number
  start_ms: number
  end_ms: number
  duration_ms: number
  position: number
  clip_ready: boolean
  rally: Rally | null
}

export interface ReelDetail {
  reel: Reel
  items: ReelItem[]
}

export interface ReelMergeResult {
  added: number
  existing: number
  total: number
  /** Present only on the session-set route, which creates or finds the reel. */
  slug?: string
  name?: string
}

export interface RenderResult {
  job_id: string
  already_running: boolean
}
```

Note `Reel.item_count` is required: `GET /api/reels` and `POST /api/reels` both return it, and those are the only two producers.

- [ ] **Step 6: Add the API client methods**

In `web/src/lib/api.ts`, extend the type import and add to the `api` object after `exportClips`:

```ts
  listReels: () => req<Reel[]>('/api/reels'),
  createReel: (name: string) =>
    req<Reel>('/api/reels', { method: 'POST', body: JSON.stringify({ name }) }),
  getReel: (slug: string) => req<ReelDetail>(`/api/reels/${slug}`),
  addReelItems: (slug: string, items: SpanRef[]) =>
    req<ReelMergeResult>(`/api/reels/${slug}/items`, {
      method: 'POST',
      body: JSON.stringify({ items }),
    }),
  removeReelItem: (slug: string, span: SpanRef) =>
    req<{ removed: boolean; total: number }>(`/api/reels/${slug}/items/remove`, {
      method: 'POST',
      body: JSON.stringify(span),
    }),
  setReelOrder: (slug: string, order: SpanRef[]) =>
    req<{ ok: boolean }>(`/api/reels/${slug}/order`, {
      method: 'POST',
      body: JSON.stringify({ order }),
    }),
  // Not routed through post(): like exportClips, its return shape is the
  // four counts plan_reel_export reports, not post()'s ok/count/id union.
  exportReelClips: (slug: string) =>
    req<ExportResult>(`/api/reels/${slug}/export`, { method: 'POST' }),
  renderReel: (slug: string) =>
    req<RenderResult>(`/api/reels/${slug}/render`, { method: 'POST' }),
  createSessionReel: (sessionId: string, which: 'points' | 'starred') =>
    req<ReelMergeResult>(`/api/sessions/${sessionId}/reels`, {
      method: 'POST',
      body: JSON.stringify({ which }),
    }),
```

`SpanRef` is the span triple every reel-membership call sends. Add it to `types.ts` beside `ReelItem`:

```ts
/** How a reel addresses an item: a span of a source, never a rally id --
 * replace_rallies deletes every rally on a sweep, so a held rally id
 * expires and a span does not. */
export interface SpanRef {
  source_id: string
  start_ms: number
  end_ms: number
}
```

- [ ] **Step 7: Write `web/src/routes/Reels.svelte`**

```svelte
<script lang="ts">
  import JobsBadge from '../components/JobsBadge.svelte'
  import { api } from '../lib/api'
  import { reelStateLabel } from '../lib/reels'
  import { navigate } from '../lib/router.svelte'
  import type { Reel } from '../lib/types'

  let reels = $state<Reel[]>([])
  let error = $state<string | null>(null)
  let loading = $state(true)
  let name = $state('')
  let creating = $state(false)

  $effect(() => {
    let cancelled = false
    loading = true
    error = null
    api
      .listReels()
      .then((r) => {
        if (!cancelled) reels = r
      })
      .catch((e) => {
        if (!cancelled) error = String(e)
      })
      .finally(() => {
        if (!cancelled) loading = false
      })
    return () => {
      cancelled = true
    }
  })

  async function create(): Promise<void> {
    // Guarded rather than merely disabled: the form submits on Enter too,
    // and a double Enter would otherwise create two reels with -2 slugs.
    if (creating || !name.trim()) return
    creating = true
    try {
      const reel = await api.createReel(name.trim())
      navigate(`/reels/${reel.slug}`)
    } catch (e) {
      error = String(e)
    } finally {
      creating = false
    }
  }
</script>

<header class="mb-6 flex items-baseline justify-between">
  <h1 class="text-xl font-semibold">Reels</h1>
  <div class="flex items-center gap-4">
    <button class="font-mono text-xs text-neutral-400 hover:text-neutral-200"
            onclick={() => navigate('/')}>Sessions</button>
    <JobsBadge />
  </div>
</header>

<form class="mb-6 flex gap-2" onsubmit={(e) => { e.preventDefault(); create() }}>
  <input
    class="flex-1 rounded border border-neutral-700 bg-neutral-900 px-3 py-1.5 text-sm"
    placeholder="New reel name"
    bind:value={name}
    aria-label="New reel name"
  />
  <button
    class="rounded border border-neutral-700 px-3 py-1.5 font-mono text-xs text-neutral-200
           hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-40"
    disabled={creating || !name.trim()}
  >
    New reel
  </button>
</form>

{#if error}
  <p class="rounded bg-red-500/10 p-3 text-sm text-red-300">{error}</p>
{:else if loading}
  <p class="text-sm text-neutral-400">Loading…</p>
{:else if reels.length === 0}
  <p class="text-sm text-neutral-400">
    No reels yet. Finish reviewing a session and compile its points, or name one above.
  </p>
{:else}
  <ul class="divide-y divide-neutral-800">
    {#each reels as r (r.id)}
      <li>
        <button
          class="flex w-full items-baseline justify-between py-3 text-left hover:bg-neutral-900"
          onclick={() => navigate(`/reels/${r.slug}`)}
        >
          <span class="font-medium">{r.name}</span>
          <span class="font-mono text-xs text-neutral-400">
            {r.item_count} clips · {reelStateLabel(r)}
          </span>
        </button>
      </li>
    {/each}
  </ul>
{/if}
```

`reelStateLabel` lands in Task 7 — this component will not typecheck until then, which is why Step 9 runs `npm run check` only after that task. Write it now anyway; the two ship together.

- [ ] **Step 8: Wire the routes**

`web/src/App.svelte`:

```svelte
<script lang="ts">
  import Library from './routes/Library.svelte'
  import Reel from './routes/Reel.svelte'
  import Reels from './routes/Reels.svelte'
  import Session from './routes/Session.svelte'
  import Setup from './routes/Setup.svelte'
  import { createRouter } from './lib/router.svelte'

  const router = createRouter()
</script>

<main class="mx-auto max-w-6xl p-6">
  {#if router.current.name === 'library'}
    <Library />
  {:else if router.current.name === 'setup'}
    <Setup id={router.current.id} />
  {:else if router.current.name === 'reels'}
    <Reels />
  {:else if router.current.name === 'reel'}
    <Reel slug={router.current.slug} />
  {:else}
    <Session id={router.current.id} />
  {/if}
</main>
```

`Reel.svelte` arrives in Task 9. Create a placeholder now so the app still builds:

```svelte
<!-- web/src/routes/Reel.svelte -- replaced wholesale in Task 9 -->
<script lang="ts">
  let { slug }: { slug: string } = $props()
</script>

<p class="text-sm text-neutral-400">Loading {slug}…</p>
```

In `web/src/routes/Library.svelte`, give the header a way in — `/reels` is otherwise reachable only by typing the hash:

```svelte
<header class="mb-6 flex items-baseline justify-between">
  <h1 class="text-xl font-semibold">Sessions</h1>
  <div class="flex items-center gap-4">
    <button class="font-mono text-xs text-neutral-400 hover:text-neutral-200"
            onclick={() => navigate('/reels')}>Reels</button>
    <JobsBadge />
  </div>
</header>
```

- [ ] **Step 9: Run the frontend tests**

```bash
cd web && npx vitest run
```

Expected: PASS. (`npm run check` will still fail on `reelStateLabel` until Task 7 — that is expected and is why it is not run here.)

- [ ] **Step 10: Commit**

```bash
git add web/src/lib/router.svelte.ts web/src/lib/types.ts web/src/lib/api.ts web/src/App.svelte web/src/routes/Library.svelte web/src/routes/Reels.svelte web/src/routes/Reel.svelte web/tests/router.test.ts
git commit -m "feat(web): reel routes, types, api client and the reels list"
```

---

### Task 7: The pure logic — reorder math and reel helpers

**Files:**
- Create: `web/src/lib/reorder.ts`, `web/src/lib/reels.ts`
- Test: `web/tests/reorder.test.ts` (create), `web/tests/reels.test.ts` (create)

**Interfaces:**
- Consumes: `web/src/lib/time.ts::clamp`, `web/src/lib/types.ts`.
- Produces:
  - `moveItem<T>(list: T[], from: number, to: number): T[]`
  - `dropIndex(pointerY: number, midpoints: number[], fromIndex: number): number`
  - `spanKey(span: SpanRef): string`
  - `spanRef(item: ReelItem | Rally & { source_id: string }): SpanRef`
  - `mergeSpans(existing: SpanRef[], incoming: SpanRef[]): SpanRef[]`
  - `missingClipCount(items: ReelItem[]): number`
  - `renderBlockedReason(items: ReelItem[]): string | null`
  - `reelStateLabel(reel: Reel): string`

- [ ] **Step 1: Write the failing tests**

Create `web/tests/reorder.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { dropIndex, moveItem } from '../src/lib/reorder'

describe('moveItem', () => {
  it('moves an item down', () => {
    expect(moveItem(['a', 'b', 'c', 'd'], 0, 2)).toEqual(['b', 'c', 'a', 'd'])
  })

  it('moves an item up', () => {
    expect(moveItem(['a', 'b', 'c', 'd'], 3, 1)).toEqual(['a', 'd', 'b', 'c'])
  })

  it('moving to the same index is a no-op', () => {
    expect(moveItem(['a', 'b', 'c'], 1, 1)).toEqual(['a', 'b', 'c'])
  })

  it('never mutates the input', () => {
    const list = ['a', 'b', 'c']
    moveItem(list, 0, 2)
    expect(list).toEqual(['a', 'b', 'c'])
  })

  it('clamps an out-of-range destination instead of dropping the item', () => {
    // A pointer dragged past the end of the list is an ordinary gesture, not
    // an error -- losing the row over it would be the worst possible answer.
    expect(moveItem(['a', 'b', 'c'], 0, 99)).toEqual(['b', 'c', 'a'])
    expect(moveItem(['a', 'b', 'c'], 2, -5)).toEqual(['c', 'a', 'b'])
  })

  it('returns a copy for an out-of-range source', () => {
    expect(moveItem(['a', 'b'], 7, 0)).toEqual(['a', 'b'])
  })
})

describe('dropIndex', () => {
  // Four 40px rows starting at y=0, so their midpoints are 20, 60, 100, 140.
  const midpoints = [20, 60, 100, 140]

  it('keeps the row where it is while the pointer stays in its own slot', () => {
    expect(dropIndex(20, midpoints, 0)).toBe(0)
  })

  it('lands after a neighbour once the pointer crosses its midpoint', () => {
    // Dragging row 0 down: the remaining midpoints are 60, 100, 140.
    expect(dropIndex(59, midpoints, 0)).toBe(0)
    expect(dropIndex(61, midpoints, 0)).toBe(1)
    expect(dropIndex(101, midpoints, 0)).toBe(2)
  })

  it('lands before a neighbour when dragging upward', () => {
    // Dragging row 3 up: the remaining midpoints are 20, 60, 100.
    expect(dropIndex(19, midpoints, 3)).toBe(0)
    expect(dropIndex(21, midpoints, 3)).toBe(1)
  })

  it('clamps past either end', () => {
    expect(dropIndex(-500, midpoints, 2)).toBe(0)
    expect(dropIndex(9999, midpoints, 2)).toBe(3)
  })

  it('is a no-op for a single-row list', () => {
    expect(dropIndex(9999, [20], 0)).toBe(0)
  })

  // The contract the component depends on: dropIndex returns an index for
  // moveItem against the ORIGINAL list, so the two compose without the
  // caller doing arithmetic.
  it('composes with moveItem', () => {
    const list = ['a', 'b', 'c', 'd']
    expect(moveItem(list, 0, dropIndex(101, midpoints, 0))).toEqual(['b', 'c', 'a', 'd'])
  })
})
```

Create `web/tests/reels.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import {
  mergeSpans,
  missingClipCount,
  reelStateLabel,
  renderBlockedReason,
  spanKey,
} from '../src/lib/reels'
import type { Reel, ReelItem, SpanRef } from '../src/lib/types'

const span = (source_id: string, start_ms: number, end_ms: number): SpanRef => ({
  source_id, start_ms, end_ms,
})

function item(overrides: Partial<ReelItem> = {}): ReelItem {
  return {
    source_id: 'src1',
    session_id: 's1',
    source_idx: 1,
    start_ms: 1000,
    end_ms: 5000,
    duration_ms: 4000,
    position: 0,
    clip_ready: true,
    rally: null,
    ...overrides,
  }
}

function reel(overrides: Partial<Reel> = {}): Reel {
  return {
    id: 'r1',
    name: 'r',
    slug: 'r',
    rendered_path: null,
    rendered_at: null,
    dirty: 1,
    created_at: '2026-08-21T10:00:00Z',
    item_count: 0,
    ...overrides,
  }
}

describe('spanKey', () => {
  it('distinguishes sources with the same span', () => {
    expect(spanKey(span('a', 1, 2))).not.toBe(spanKey(span('b', 1, 2)))
  })

  it('is stable for the same span', () => {
    expect(spanKey(span('a', 1, 2))).toBe(spanKey(span('a', 1, 2)))
  })
})

describe('mergeSpans', () => {
  it('appends only what is new, in the order given', () => {
    expect(mergeSpans([span('a', 1, 2)], [span('a', 3, 4), span('a', 5, 6)])).toEqual([
      span('a', 1, 2), span('a', 3, 4), span('a', 5, 6),
    ])
  })

  it('never removes or reorders what is already there', () => {
    // The whole point: a second click must not discard a manual reorder.
    const existing = [span('a', 5, 6), span('a', 1, 2)]
    expect(mergeSpans(existing, [span('a', 1, 2)])).toEqual(existing)
  })

  it('drops a duplicate within the incoming list too', () => {
    expect(mergeSpans([], [span('a', 1, 2), span('a', 1, 2)])).toEqual([span('a', 1, 2)])
  })

  it('does not mutate its inputs', () => {
    const existing = [span('a', 1, 2)]
    mergeSpans(existing, [span('a', 3, 4)])
    expect(existing).toEqual([span('a', 1, 2)])
  })
})

describe('missingClipCount', () => {
  it('counts items with no clip on disk', () => {
    expect(missingClipCount([item(), item({ clip_ready: false })])).toBe(1)
  })
})

describe('renderBlockedReason', () => {
  it('names the count so the button says why it is disabled', () => {
    expect(renderBlockedReason([item({ clip_ready: false }), item({ clip_ready: false })]))
      .toBe('2 clips not cut yet')
  })

  it('says "clip" for one', () => {
    expect(renderBlockedReason([item({ clip_ready: false })])).toBe('1 clip not cut yet')
  })

  it('blocks an empty reel', () => {
    expect(renderBlockedReason([])).toBe('No clips in this reel yet')
  })

  it('returns null when every clip is ready', () => {
    expect(renderBlockedReason([item(), item()])).toBeNull()
  })
})

describe('reelStateLabel', () => {
  it('reads "not rendered" before the first render', () => {
    expect(reelStateLabel(reel())).toBe('not rendered')
  })

  it('distinguishes a stale render from no render at all', () => {
    // rendered_path survives a membership change on purpose (see
    // mark_dirty): the file is still on disk and still watchable, it is
    // merely out of date, and collapsing the two states would hide that.
    expect(reelStateLabel(reel({
      rendered_path: 'reels/r.mp4', rendered_at: '2026-08-21T12:00:00Z', dirty: 1,
    }))).toBe('needs re-render')
  })

  it('reads "rendered" when clean', () => {
    expect(reelStateLabel(reel({
      rendered_path: 'reels/r.mp4', rendered_at: '2026-08-21T12:00:00Z', dirty: 0,
    }))).toBe('rendered')
  })
})
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd web && npx vitest run tests/reorder.test.ts tests/reels.test.ts
```

Expected: FAIL — cannot resolve `../src/lib/reorder`.

- [ ] **Step 3: Write `web/src/lib/reorder.ts`**

```ts
import { clamp } from './time'

/**
 * `list` with the item at `from` moved to `to`.
 *
 * Pure and non-mutating, like every other module under lib/: the component
 * owns only `getBoundingClientRect`, and everything that decides where a row
 * lands is testable without a DOM. Drag-to-reorder is hand-rolled here for
 * the same reason ZoomBand and QuadEditor are -- a drag library would be a
 * dependency for arithmetic this small.
 *
 * `to` is clamped rather than validated: a pointer dragged past the end of
 * the list is an ordinary gesture, and losing the row over it is the worst
 * available answer. An out-of-range `from`, by contrast, is a caller bug
 * with no sensible interpretation, so it returns the list unchanged.
 */
export function moveItem<T>(list: T[], from: number, to: number): T[] {
  if (from < 0 || from >= list.length) return [...list]
  if (from === to) return [...list]
  const next = [...list]
  const [item] = next.splice(from, 1)
  next.splice(clamp(to, 0, next.length), 0, item)
  return next
}

/**
 * The index `moveItem` should be given for a drag currently at `pointerY`.
 *
 * `midpoints` are the vertical centres of every row IN LIST ORDER, in the
 * same coordinate space as `pointerY` (viewport pixels, straight off
 * getBoundingClientRect). Midpoints rather than edges: a row swaps when the
 * pointer passes the middle of its neighbour, which is what makes the
 * gesture feel like the row is displacing the one it crosses.
 *
 * The dragged row's own midpoint is excluded before counting, which is what
 * makes the result directly usable as `moveItem(list, from, dropIndex(...))`
 * -- moveItem removes the item before splicing it back, so both functions
 * are reasoning about the same shortened list. Doing this any other way
 * means off-by-one arithmetic in the component, which is exactly what this
 * module exists to keep out of there.
 */
export function dropIndex(pointerY: number, midpoints: number[], fromIndex: number): number {
  const rest = midpoints.filter((_, i) => i !== fromIndex)
  let index = 0
  while (index < rest.length && pointerY > rest[index]) index++
  return index
}
```

- [ ] **Step 4: Write `web/src/lib/reels.ts`**

```ts
import type { Reel, ReelItem, SpanRef } from './types'

/**
 * A reel item's identity: the span of a source, never a rally id.
 *
 * `replace_rallies` deletes every rally for a source on each threshold
 * sweep, so a held rally id expires; a span does not, and it is what the
 * clip on disk is named for. Same key `reel_items`' primary key uses.
 */
export function spanKey(span: SpanRef): string {
  return `${span.source_id}:${span.start_ms}:${span.end_ms}`
}

export function spanRef(span: SpanRef): SpanRef {
  // A narrowing copy: ReelItem and Rally both carry the three fields plus a
  // lot else, and POSTing the whole object would send the server fields it
  // ignores today and might not ignore later.
  return { source_id: span.source_id, start_ms: span.start_ms, end_ms: span.end_ms }
}

/**
 * `existing` with every span of `incoming` it does not already hold appended.
 *
 * Additive, never a replacement: existing entries and the order a human
 * dragged them into are untouched. Overwriting membership would silently
 * discard a manual reorder -- the same class of mistake replace_rallies
 * makes with boundary edits, which already cost this project a 9.6-second
 * rally. Mirrors what `add_items` does server-side; used here so the picker
 * can show the result of an add before committing to it.
 */
export function mergeSpans(existing: SpanRef[], incoming: SpanRef[]): SpanRef[] {
  const seen = new Set(existing.map(spanKey))
  const out = [...existing]
  for (const span of incoming) {
    const key = spanKey(span)
    if (seen.has(key)) continue
    seen.add(key)
    out.push(spanRef(span))
  }
  return out
}

export function missingClipCount(items: ReelItem[]): number {
  return items.filter((i) => !i.clip_ready).length
}

/**
 * Why Render is disabled, or null if it is not.
 *
 * The count is in the string on purpose: §5.1 requires render to refuse
 * while any clip is missing and to NAME how many, so the reason is visible
 * on the button rather than discovered by pressing it. Render never
 * auto-enqueues the cuts -- that would turn one button into half an hour of
 * encoding -- so the user needs to know what to press instead.
 */
export function renderBlockedReason(items: ReelItem[]): string | null {
  if (items.length === 0) return 'No clips in this reel yet'
  const missing = missingClipCount(items)
  if (missing === 0) return null
  return `${missing} ${missing === 1 ? 'clip' : 'clips'} not cut yet`
}

/**
 * A reel's render state for the list page.
 *
 * Three states, not two: `rendered_path` survives a membership change (see
 * mark_dirty), because the file is still on disk and still watchable, it is
 * merely out of date. Collapsing "never rendered" and "stale" would hide
 * that there is something to watch right now.
 */
export function reelStateLabel(reel: Reel): string {
  if (!reel.rendered_path) return 'not rendered'
  return reel.dirty ? 'needs re-render' : 'rendered'
}
```

- [ ] **Step 5: Run to verify they pass**

```bash
cd web && npx vitest run tests/reorder.test.ts tests/reels.test.ts
```

Expected: PASS, 22 tests.

- [ ] **Step 6: Typecheck**

```bash
cd web && npm run check
```

Expected: 0 errors (`Reels.svelte` from Task 6 now resolves `reelStateLabel`). `Reel.svelte` is still the placeholder, which typechecks fine.

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/reorder.ts web/src/lib/reels.ts web/tests/reorder.test.ts web/tests/reels.test.ts
git commit -m "feat(web): pure reorder math and reel helpers"
```

---

### Task 8: The drag-to-reorder list

**Files:**
- Create: `web/src/components/ReelItemList.svelte`
- Test: `web/tests/reel-item-list.test.ts` (create)

**Interfaces:**
- Consumes: `moveItem`, `dropIndex` (Task 7); `formatDuration` from `lib/time`; `api.frameUrl`.
- Produces a component with props:
  ```ts
  interface Props {
    items: ReelItem[]
    /** Fired once, on pointerup, with the reordered list. Never fired
     * mid-drag -- a drag fires dozens of pointermoves and each one would be
     * a POST. Same split ZoomBand draws between onchange and oncommit. */
    oncommit: (items: ReelItem[]) => void
    onremove: (item: ReelItem) => void
  }
  ```

- [ ] **Step 1: Write the failing test**

Create `web/tests/reel-item-list.test.ts`:

```ts
import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReelItem } from '../src/lib/types'

vi.mock('../src/lib/api', () => ({
  api: { frameUrl: () => 'about:blank' },
}))

const { default: ReelItemList } = await import('../src/components/ReelItemList.svelte')

function item(start: number, overrides: Partial<ReelItem> = {}): ReelItem {
  return {
    source_id: 'src1',
    session_id: 's1',
    source_idx: 1,
    start_ms: start,
    end_ms: start + 4000,
    duration_ms: 4000,
    position: 0,
    clip_ready: true,
    rally: null,
    ...overrides,
  }
}

let host: HTMLElement
let component: ReturnType<typeof mount> | null = null

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
})

afterEach(() => {
  if (component) unmount(component)
  component = null
  host.remove()
})

function rows(): HTMLElement[] {
  return [...host.querySelectorAll('[data-reel-row]')] as HTMLElement[]
}

/** jsdom gives every element a zero rect, so the component's only DOM read
 * has to be stubbed for the drag to mean anything. 40px rows from y=0. */
function stubRects(): void {
  rows().forEach((row, i) => {
    row.getBoundingClientRect = () =>
      ({ top: i * 40, bottom: i * 40 + 40, height: 40, left: 0, right: 100,
         width: 100, x: 0, y: i * 40, toJSON: () => ({}) }) as DOMRect
  })
}

function drag(fromRow: number, toClientY: number): void {
  const handle = rows()[fromRow].querySelector('[data-drag-handle]') as HTMLElement
  handle.setPointerCapture = vi.fn()
  handle.releasePointerCapture = vi.fn()
  handle.dispatchEvent(new PointerEvent('pointerdown', {
    bubbles: true, pointerId: 1, clientY: fromRow * 40 + 20,
  }))
  flushSync()
  window.dispatchEvent(new PointerEvent('pointermove', {
    bubbles: true, pointerId: 1, clientY: toClientY,
  }))
  flushSync()
  window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1 }))
  flushSync()
}

describe('ReelItemList', () => {
  it('renders one row per item with its duration', () => {
    component = mount(ReelItemList, {
      target: host,
      props: { items: [item(1000), item(9000)], oncommit: vi.fn(), onremove: vi.fn() },
    })
    flushSync()
    expect(rows()).toHaveLength(2)
    expect(host.textContent).toContain('4.0s')
  })

  it('badges an item whose clip is not cut', () => {
    component = mount(ReelItemList, {
      target: host,
      props: {
        items: [item(1000, { clip_ready: false })],
        oncommit: vi.fn(), onremove: vi.fn(),
      },
    })
    flushSync()
    expect(host.textContent).toContain('missing')
  })

  it('badges an orphan without hiding it', () => {
    // §5: an item whose rally vanished renders as orphaned but stays
    // playable and renderable, never silently dropped.
    component = mount(ReelItemList, {
      target: host,
      props: { items: [item(1000, { rally: null })], oncommit: vi.fn(), onremove: vi.fn() },
    })
    flushSync()
    expect(rows()).toHaveLength(1)
    expect(host.textContent).toContain('orphan')
  })

  it('commits a reorder once, on release', () => {
    const oncommit = vi.fn()
    component = mount(ReelItemList, {
      target: host,
      props: { items: [item(1000), item(9000), item(20000)], oncommit, onremove: vi.fn() },
    })
    flushSync()
    stubRects()

    drag(0, 101) // past the midpoint of row 2 (y=100)

    expect(oncommit).toHaveBeenCalledTimes(1)
    expect(oncommit.mock.calls[0][0].map((i: ReelItem) => i.start_ms))
      .toEqual([9000, 20000, 1000])
  })

  it('does not commit when the row lands where it started', () => {
    // A click on the handle is not a reorder. Firing anyway would mark the
    // reel dirty and demand a re-render for nothing.
    const oncommit = vi.fn()
    component = mount(ReelItemList, {
      target: host,
      props: { items: [item(1000), item(9000)], oncommit, onremove: vi.fn() },
    })
    flushSync()
    stubRects()

    drag(0, 20)

    expect(oncommit).not.toHaveBeenCalled()
  })

  it('removes an item', () => {
    const onremove = vi.fn()
    component = mount(ReelItemList, {
      target: host,
      props: { items: [item(1000), item(9000)], oncommit: vi.fn(), onremove },
    })
    flushSync()
    ;(rows()[1].querySelector('[data-remove]') as HTMLElement).click()
    flushSync()
    expect(onremove.mock.calls[0][0].start_ms).toBe(9000)
  })

  it('reorders from the keyboard', () => {
    // The drag handle is a button, and a pointer-only reorder is unreachable
    // without a mouse. Alt+Arrow moves the focused row, which is also the
    // only reorder path svelte-check's a11y rules will accept on a div.
    const oncommit = vi.fn()
    component = mount(ReelItemList, {
      target: host,
      props: { items: [item(1000), item(9000)], oncommit, onremove: vi.fn() },
    })
    flushSync()
    const handle = rows()[0].querySelector('[data-drag-handle]') as HTMLElement
    handle.dispatchEvent(new KeyboardEvent('keydown', {
      bubbles: true, key: 'ArrowDown', altKey: true,
    }))
    flushSync()
    expect(oncommit.mock.calls[0][0].map((i: ReelItem) => i.start_ms)).toEqual([9000, 1000])
  })
})
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd web && npx vitest run tests/reel-item-list.test.ts
```

Expected: FAIL — cannot resolve `../src/components/ReelItemList.svelte`.

- [ ] **Step 3: Write `web/src/components/ReelItemList.svelte`**

```svelte
<script lang="ts">
  import { api } from '../lib/api'
  import { dropIndex, moveItem } from '../lib/reorder'
  import { formatDuration } from '../lib/time'
  import type { ReelItem } from '../lib/types'

  interface Props {
    items: ReelItem[]
    /** Fired once, on release, with the reordered list. Never fired
     * mid-drag: a drag produces dozens of pointermoves and each one would
     * be a POST. Same split ZoomBand draws between onchange and oncommit,
     * except there is no local-only preview to fire here -- `order` below
     * IS the preview. */
    oncommit: (items: ReelItem[]) => void
    onremove: (item: ReelItem) => void
  }

  let { items, oncommit, onremove }: Props = $props()

  let list = $state<HTMLElement>()
  let dragging = $state<number | null>(null)
  // The live, in-progress order. Kept here rather than read back off the
  // `items` prop so the commit fired on release is exactly what this drag
  // produced, independent of whether the parent's prop echo has flushed.
  let order = $state<ReelItem[]>([])

  // What renders: the drag's working order while one is in progress, the
  // prop otherwise. The parent refetches after a commit, so this hands back
  // over cleanly once the server answers.
  const shown = $derived(dragging === null ? items : order)

  function rowMidpoints(): number[] {
    // The component's ONLY DOM read. Everything that decides where a row
    // lands is in lib/reorder.ts, because jsdom cannot help us here and
    // arithmetic in a .svelte file is untestable.
    if (!list) return []
    return [...list.querySelectorAll('[data-reel-row]')].map((el) => {
      const r = el.getBoundingClientRect()
      return r.top + r.height / 2
    })
  }

  function startDrag(index: number, e: PointerEvent): void {
    e.preventDefault()
    dragging = index
    order = [...items]
    ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
  }

  function onPointerMove(e: PointerEvent): void {
    if (dragging === null) return
    const to = dropIndex(e.clientY, rowMidpoints(), dragging)
    if (to === dragging) return
    order = moveItem(order, dragging, to)
    dragging = to
  }

  function endDrag(): void {
    if (dragging === null) return
    const moved = order
    const unchanged =
      moved.length === items.length &&
      moved.every((item, i) => item === items[i])
    dragging = null
    // A click on the handle is not a reorder. Committing anyway would mark
    // the reel dirty and demand a re-render for a gesture that changed
    // nothing.
    if (!unchanged) oncommit(moved)
  }

  function onHandleKey(index: number, e: KeyboardEvent): void {
    // Alt+Arrow, not bare Arrow: the handle is a button inside a scrolling
    // list, and swallowing plain arrows would break scrolling for keyboard
    // users. A pointer-only reorder is unreachable without a mouse, so this
    // is the accessible path, not a convenience.
    if (!e.altKey) return
    const to = e.key === 'ArrowUp' ? index - 1 : e.key === 'ArrowDown' ? index + 1 : null
    if (to === null || to < 0 || to >= items.length) return
    e.preventDefault()
    oncommit(moveItem(items, index, to))
  }
</script>

<svelte:window onpointermove={onPointerMove} onpointerup={endDrag} onpointercancel={endDrag} />

<ul bind:this={list} class="divide-y divide-neutral-800">
  {#each shown as item, i (`${item.source_id}:${item.start_ms}:${item.end_ms}`)}
    <li
      data-reel-row
      class="flex items-center gap-3 py-2 {dragging === i ? 'opacity-50' : ''}"
    >
      <button
        data-drag-handle
        class="cursor-grab select-none px-2 font-mono text-neutral-500 hover:text-neutral-200"
        aria-label="Reorder {i + 1}. Hold alt and press the up or down arrow."
        onpointerdown={(e) => startDrag(i, e)}
        onkeydown={(e) => onHandleKey(i, e)}
      >⠿</button>

      <!-- lazy: a 24-item reel would otherwise fire 24 on-demand frame
           extractions at once, each an ffmpeg call on the shared request
           thread pool. -->
      <img
        class="h-10 w-16 rounded bg-black object-cover"
        src={api.frameUrl(item.session_id, item.source_idx, item.start_ms)}
        alt=""
        loading="lazy"
      />

      <span class="font-mono text-xs tabular-nums text-neutral-300">
        {formatDuration(item.duration_ms)}
      </span>
      <span class="font-mono text-xs text-neutral-500">
        source {String(item.source_idx).padStart(2, '0')}
      </span>

      {#if !item.clip_ready}
        <span class="rounded bg-amber-500/15 px-2 py-0.5 font-mono text-xs text-amber-300">
          missing
        </span>
      {:else}
        <span class="rounded bg-neutral-800 px-2 py-0.5 font-mono text-xs text-neutral-400">
          ready
        </span>
      {/if}

      {#if item.rally === null}
        <!-- The rally this span came from is gone (a threshold sweep
             re-makes every rally). The clip is not: it is what the reel is
             actually made of, so this is badged, never dropped. -->
        <span class="rounded bg-neutral-800 px-2 py-0.5 font-mono text-xs text-neutral-500">
          orphan
        </span>
      {/if}

      <button
        data-remove
        class="ml-auto px-2 font-mono text-xs text-neutral-500 hover:text-red-300"
        aria-label="Remove clip {i + 1}"
        onclick={() => onremove(item)}
      >✕</button>
    </li>
  {/each}
</ul>
```

- [ ] **Step 4: Run to verify it passes**

```bash
cd web && npx vitest run tests/reel-item-list.test.ts
```

Expected: PASS, 7 tests.

- [ ] **Step 5: Typecheck and full frontend suite**

```bash
cd web && npm run check && npx vitest run
```

Expected: 0 errors, all tests pass. If svelte-check flags an a11y rule on the row, do **not** silence it — the Alt+Arrow handler and the `aria-label`s exist to satisfy it honestly.

- [ ] **Step 6: Commit**

```bash
git add web/src/components/ReelItemList.svelte web/tests/reel-item-list.test.ts
git commit -m "feat(web): drag-to-reorder reel item list"
```

---

### Task 9: Preview — a controller and a `VideoDeck` shell

**Files:**
- Create: `web/src/lib/reelPreview.ts`, `web/src/components/ReelPreview.svelte`
- Test: `web/tests/reel-preview.test.ts` (create)

The module is `reelPreview.ts`, **not** `preview.ts` — that name is taken by the setup wizard's frame-grid helper (`previewTimestamps`).

**Interfaces:**
- Consumes: `web/src/components/VideoDeck.svelte`, `api.proxyUrl`, `web/src/lib/types.ts::ReelItem`.
- Produces:
  - `class ReelPreviewController` — `constructor(items: ReelItem[])`, getters `current`, `next`, `index`, `total`, `finished`, methods `advance()`, `restart()`, `jumpTo(index: number)`.

- [ ] **Step 1: Write the failing tests**

Create `web/tests/reel-preview.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { ReelPreviewController } from '../src/lib/reelPreview'
import type { ReelItem } from '../src/lib/types'

function item(start: number, overrides: Partial<ReelItem> = {}): ReelItem {
  return {
    source_id: 'src1',
    session_id: 's1',
    source_idx: 1,
    start_ms: start,
    end_ms: start + 4000,
    duration_ms: 4000,
    position: 0,
    clip_ready: true,
    rally: null,
    ...overrides,
  }
}

describe('ReelPreviewController', () => {
  it('starts on the first item', () => {
    const c = new ReelPreviewController([item(1000), item(9000)])
    expect(c.index).toBe(0)
    expect(c.current?.start_ms).toBe(1000)
    expect(c.total).toBe(2)
    expect(c.finished).toBe(false)
  })

  it('exposes the next item so VideoDeck can preload it', () => {
    // The whole reason preview seeks the proxy instead of chaining clip
    // files: VideoDeck already preloads the next span into its second
    // element, across different sources.
    const c = new ReelPreviewController([item(1000), item(9000)])
    expect(c.next?.start_ms).toBe(9000)
  })

  it('advances in reel order, not chronological order', () => {
    // A hand-reordered reel plays as ordered. Sorting here would silently
    // undo the drag.
    const c = new ReelPreviewController([item(9000), item(1000)])
    expect(c.current?.start_ms).toBe(9000)
    c.advance()
    expect(c.current?.start_ms).toBe(1000)
  })

  it('finishes past the last item rather than looping', () => {
    const c = new ReelPreviewController([item(1000)])
    expect(c.next).toBeUndefined()
    c.advance()
    expect(c.finished).toBe(true)
    expect(c.current).toBeUndefined()
  })

  it('does not run past the end on a repeated advance', () => {
    const c = new ReelPreviewController([item(1000)])
    c.advance()
    c.advance()
    expect(c.index).toBe(1)
  })

  it('restarts from the top', () => {
    const c = new ReelPreviewController([item(1000), item(9000)])
    c.advance()
    c.advance()
    c.restart()
    expect(c.index).toBe(0)
    expect(c.finished).toBe(false)
  })

  it('jumps to an item', () => {
    const c = new ReelPreviewController([item(1000), item(9000), item(20000)])
    c.jumpTo(2)
    expect(c.current?.start_ms).toBe(20000)
  })

  it('ignores an out-of-range jump', () => {
    // The list can shrink under the preview (a removal in the builder), and
    // silently seeking to nothing is worse than staying put.
    const c = new ReelPreviewController([item(1000)])
    c.jumpTo(5)
    expect(c.index).toBe(0)
  })

  it('is immediately finished for an empty reel', () => {
    const c = new ReelPreviewController([])
    expect(c.finished).toBe(true)
    expect(c.total).toBe(0)
  })

  it('previews items whose clip is not cut yet', () => {
    // Preview plays the PROXY, so it is unaffected by whether the 4K clip
    // exists -- that is the point of §6.5. Filtering to ready items would
    // hide exactly the spans a reviewer is about to cut.
    const c = new ReelPreviewController([item(1000, { clip_ready: false })])
    expect(c.current?.start_ms).toBe(1000)
  })
})
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd web && npx vitest run tests/reel-preview.test.ts
```

Expected: FAIL — cannot resolve `../src/lib/reelPreview`.

- [ ] **Step 3: Write `web/src/lib/reelPreview.ts`**

```ts
import type { ReelItem } from './types'

/**
 * Preview's ordering and advance logic.
 *
 * Deliberately pure: no DOM, no fetch, no timers -- the same rule
 * QueueController follows, and for the same reason (jsdom has no `<video>`).
 * The component is a shell that hands `current`/`next` to VideoDeck and
 * calls `advance()` from its `onended`.
 *
 * Preview plays the PROXY, seeking to each item's span in order, rather
 * than chaining clip files. That reuses VideoDeck exactly as built -- it
 * already takes a source plus in/out points, fires onended at the out-point,
 * and preloads the next span into its second element even across sources.
 * Its limits, stated rather than discovered later: it shows 1080p, and it
 * cannot reveal `-c copy` artifacts (that is what the reel job's duration
 * probe is for). What it does show exactly is TIMING, because reel items
 * carry their own start_ms/end_ms -- so it plays precisely the span the
 * clip contains.
 */
export class ReelPreviewController {
  #items: ReelItem[]
  #index = 0

  constructor(items: ReelItem[]) {
    // Not filtered to clip_ready: preview reads the proxy, so an uncut span
    // previews perfectly well -- and those are exactly the spans a reviewer
    // is deciding whether to cut.
    this.#items = [...items]
  }

  get current(): ReelItem | undefined {
    return this.#items[this.#index]
  }

  get next(): ReelItem | undefined {
    return this.#items[this.#index + 1]
  }

  get index(): number {
    return this.#index
  }

  get total(): number {
    return this.#items.length
  }

  get finished(): boolean {
    return this.#index >= this.#items.length
  }

  advance(): void {
    // Clamped at one past the end rather than allowed to run away: `finished`
    // is a comparison against length, and an unbounded index would keep
    // incrementing on every stray onended a torn-down deck still fires.
    if (this.#index < this.#items.length) this.#index += 1
  }

  restart(): void {
    this.#index = 0
  }

  jumpTo(index: number): void {
    // Silently ignores out of range: the item list can shrink under the
    // preview (a removal in the builder), and seeking to nothing is worse
    // than staying put.
    if (index >= 0 && index < this.#items.length) this.#index = index
  }
}
```

- [ ] **Step 4: Run to verify they pass**

```bash
cd web && npx vitest run tests/reel-preview.test.ts
```

Expected: PASS, 10 tests.

- [ ] **Step 5: Write `web/src/components/ReelPreview.svelte`**

```svelte
<script lang="ts">
  import { untrack } from 'svelte'
  import { api } from '../lib/api'
  import { ReelPreviewController } from '../lib/reelPreview'
  import type { ReelItem } from '../lib/types'
  import VideoDeck from './VideoDeck.svelte'

  interface Props {
    items: ReelItem[]
    onclose: () => void
  }

  let { items, onclose }: Props = $props()

  // One-time snapshot, like QueueMode's QueueController: the parent mounts
  // this inside a {#key} on the item list, so a membership change remounts
  // it fresh rather than mutating a preview mid-playback.
  const preview = new ReelPreviewController(untrack(() => items))
  let version = $state(0) // bumped to re-read the controller after a mutation
  let deck = $state<VideoDeck>()

  // `version` is the dependency that forces the re-read -- the controller is
  // a plain class, so a getter read registers no signal and the template
  // would render once and freeze. Same pattern QueueMode uses.
  const current = $derived.by(() => {
    version
    return preview.current
  })
  const next = $derived.by(() => {
    version
    return preview.next
  })
  const position = $derived.by(() => {
    version
    return { index: preview.index, total: preview.total, finished: preview.finished }
  })

  const proxy = (item: ReelItem) => api.proxyUrl(item.session_id, item.source_idx)

  function onended(): void {
    preview.advance()
    version += 1
  }

  function restart(): void {
    preview.restart()
    version += 1
  }
</script>

<div class="rounded-lg border border-neutral-800 p-4">
  <div class="mb-3 flex items-baseline justify-between">
    <h2 class="text-sm font-semibold">Preview</h2>
    <div class="flex items-center gap-4 font-mono text-xs text-neutral-400">
      <!-- Stated rather than discovered later: this is the 1080p proxy, and
           it cannot reveal a -c copy artifact. It shows TIMING exactly. -->
      <span>1080p proxy · timing only</span>
      <span class="tabular-nums">
        {Math.min(position.index + 1, position.total)} / {position.total}
      </span>
      <button class="hover:text-neutral-200" onclick={onclose}>close</button>
    </div>
  </div>

  {#if current}
    <VideoDeck
      bind:this={deck}
      src={proxy(current)}
      startMs={current.start_ms}
      endMs={current.end_ms}
      nextSrc={next ? proxy(next) : undefined}
      nextStartMs={next?.start_ms}
      {onended}
    />
  {:else}
    <div class="flex h-40 flex-col items-center justify-center gap-3 rounded bg-black">
      <p class="font-mono text-xs text-neutral-400">
        {position.total === 0 ? 'Nothing in this reel yet.' : 'End of reel.'}
      </p>
      {#if position.total > 0}
        <button
          class="rounded border border-neutral-700 px-3 py-1.5 font-mono text-xs
                 text-neutral-200 hover:bg-neutral-800"
          onclick={restart}
        >
          Play again
        </button>
      {/if}
    </div>
  {/if}
</div>
```

- [ ] **Step 6: Typecheck and full frontend suite**

```bash
cd web && npm run check && npx vitest run
```

Expected: 0 errors, all tests pass. `ReelPreview.svelte` itself is verified by hand — jsdom has no `<video>`, which is exactly why the controller carries the logic.

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/reelPreview.ts web/src/components/ReelPreview.svelte web/tests/reel-preview.test.ts
git commit -m "feat(web): reel preview by seeking the proxy"
```

---

### Task 10: The add-rallies picker

**Files:**
- Create: `web/src/components/AddRalliesPicker.svelte`
- Test: `web/tests/add-rallies-picker.test.ts` (create)

**Interfaces:**
- Consumes: `api.listSessions`, `api.getSession`, `spanKey`, `spanRef` (Task 7), `formatDuration`, `formatTs`.
- Produces a component with props:
  ```ts
  interface Props {
    /** Spans already in the reel, so they render checked-and-disabled
     * rather than as a silently ignored add. */
    existing: SpanRef[]
    /** The session to open on -- the builder passes its last item's, so the
     * common case needs no session choice at all. */
    defaultSessionId?: string | null
    onadd: (spans: SpanRef[]) => void
    onclose: () => void
  }
  ```

- [ ] **Step 1: Write the failing test**

Create `web/tests/add-rallies-picker.test.ts`:

```ts
import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Rally, Session, SessionDetail } from '../src/lib/types'

function rally(id: string, idx: number, overrides: Partial<Rally> = {}): Rally {
  return {
    id,
    session_id: 's1',
    source_id: 'src1',
    idx,
    start_ms: idx * 10000,
    end_ms: idx * 10000 + 4000,
    det_start_ms: idx * 10000,
    det_end_ms: idx * 10000 + 4000,
    confidence: 0.9,
    starred: 0,
    rejected: 0,
    point: 0,
    reviewed_at: null,
    ...overrides,
  }
}

const sessions: Session[] = [{
  id: 's1', title: '2026-08-18', played_on: '2026-08-18', status: 'reviewed',
  rally_count: 4, starred_count: 1, point_count: 2,
}]

const detail: SessionDetail = {
  session: { id: 's1', title: '2026-08-18', played_on: '2026-08-18', status: 'reviewed' },
  sources: [],
  rallies: [
    rally('r1', 1, { point: 1 }),
    rally('r2', 2, { point: 1, starred: 1 }),
    rally('r3', 3),
    rally('r4', 4, { point: 1, rejected: 1 }),
  ],
}

const mockApi = {
  listSessions: vi.fn().mockResolvedValue(sessions),
  getSession: vi.fn().mockResolvedValue(detail),
}
vi.mock('../src/lib/api', () => ({ api: mockApi }))

const { default: AddRalliesPicker } = await import('../src/components/AddRalliesPicker.svelte')

let host: HTMLElement
let component: ReturnType<typeof mount> | null = null

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
})

afterEach(() => {
  if (component) unmount(component)
  component = null
  host.remove()
})

function boxes(): HTMLInputElement[] {
  return [...host.querySelectorAll('input[type=checkbox]')] as HTMLInputElement[]
}

async function settle(): Promise<void> {
  await Promise.resolve()
  await Promise.resolve()
  flushSync()
}

async function open(props: Record<string, unknown> = {}) {
  component = mount(AddRalliesPicker, {
    target: host,
    props: { existing: [], defaultSessionId: 's1', onadd: vi.fn(), onclose: vi.fn(), ...props },
  })
  flushSync()
  await settle()
}

describe('AddRalliesPicker', () => {
  it('opens on the points filter and hides rejected rallies', async () => {
    await open()
    // r1 and r2 are points; r4 is a point but rejected, and a rejected rally
    // is a bad detection -- it is not a clip anyone wants in a reel.
    expect(boxes()).toHaveLength(2)
  })

  it('switches to starred', async () => {
    await open()
    ;(host.querySelector('[data-filter="starred"]') as HTMLElement).click()
    flushSync()
    expect(boxes()).toHaveLength(1)
  })

  it('switches to all, still without rejected', async () => {
    await open()
    ;(host.querySelector('[data-filter="all"]') as HTMLElement).click()
    flushSync()
    expect(boxes()).toHaveLength(3)
  })

  it('shows a span already in the reel as checked and disabled', async () => {
    // Not hidden: a reviewer scanning for what is missing needs to see that
    // the rally is accounted for, and an add that silently did nothing is
    // the confusing alternative.
    await open({ existing: [{ source_id: 'src1', start_ms: 10000, end_ms: 14000 }] })
    expect(boxes()[0].checked).toBe(true)
    expect(boxes()[0].disabled).toBe(true)
  })

  it('adds only the newly checked spans', async () => {
    const onadd = vi.fn()
    await open({ onadd })
    boxes()[1].click()
    flushSync()
    ;(host.querySelector('[data-add]') as HTMLElement).click()
    flushSync()
    expect(onadd).toHaveBeenCalledWith([
      { source_id: 'src1', start_ms: 20000, end_ms: 24000 },
    ])
  })

  it('disables Add while nothing is checked', async () => {
    await open()
    expect((host.querySelector('[data-add]') as HTMLButtonElement).disabled).toBe(true)
  })

  it('select-all checks every enabled row', async () => {
    const onadd = vi.fn()
    await open({ onadd })
    ;(host.querySelector('[data-select-all]') as HTMLElement).click()
    flushSync()
    ;(host.querySelector('[data-add]') as HTMLElement).click()
    flushSync()
    expect(onadd.mock.calls[0][0]).toHaveLength(2)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd web && npx vitest run tests/add-rallies-picker.test.ts
```

Expected: FAIL — cannot resolve `../src/components/AddRalliesPicker.svelte`.

- [ ] **Step 3: Write `web/src/components/AddRalliesPicker.svelte`**

```svelte
<script lang="ts">
  import { api } from '../lib/api'
  import { spanKey, spanRef } from '../lib/reels'
  import { formatDuration, formatTs } from '../lib/time'
  import type { Rally, Session, SpanRef } from '../lib/types'

  type Filter = 'points' | 'starred' | 'all'

  interface Props {
    existing: SpanRef[]
    defaultSessionId?: string | null
    onadd: (spans: SpanRef[]) => void
    onclose: () => void
  }

  let { existing, defaultSessionId = null, onadd, onclose }: Props = $props()

  let sessions = $state<Session[]>([])
  let sessionId = $state<string | null>(defaultSessionId)
  let rallies = $state<Rally[]>([])
  let filter = $state<Filter>('points')
  let checked = $state(new Set<string>())
  let error = $state<string | null>(null)
  let loading = $state(true)

  const alreadyIn = $derived(new Set(existing.map(spanKey)))

  $effect(() => {
    let cancelled = false
    api
      .listSessions()
      .then((s) => {
        if (cancelled) return
        sessions = s
        // A reel is session-agnostic, so a hand-made one has no session to
        // default to -- fall back to the newest, which is what a reviewer
        // building a reel by hand almost always wants.
        if (!sessionId && s.length > 0) sessionId = s[0].id
      })
      .catch((e) => {
        if (!cancelled) error = String(e)
      })
    return () => {
      cancelled = true
    }
  })

  $effect(() => {
    const id = sessionId
    if (!id) return
    let cancelled = false
    loading = true
    api
      .getSession(id)
      .then((d) => {
        if (cancelled) return
        rallies = d.rallies
        // Cleared on a session change: a checked span from the previous
        // session would otherwise be added invisibly, since it is no longer
        // rendered anywhere in this list.
        checked = new Set()
      })
      .catch((e) => {
        if (!cancelled) error = String(e)
      })
      .finally(() => {
        if (!cancelled) loading = false
      })
    return () => {
      cancelled = true
    }
  })

  const shown = $derived(
    rallies
      // Rejected is a ruling that this was never a rally at all -- a bad
      // detection is not a clip anyone wants in a reel, under any filter.
      .filter((r) => !r.rejected)
      .filter((r) => (filter === 'points' ? r.point : filter === 'starred' ? r.starred : true)),
  )

  const selectable = $derived(shown.filter((r) => !alreadyIn.has(spanKey(r))))
  const selected = $derived(selectable.filter((r) => checked.has(r.id)))

  function toggle(rally: Rally): void {
    // Reassigned rather than mutated: Svelte 5 tracks the binding, and an
    // in-place Set.add() would not re-render the list.
    const next = new Set(checked)
    if (next.has(rally.id)) next.delete(rally.id)
    else next.add(rally.id)
    checked = next
  }

  function selectAll(): void {
    checked = new Set(selectable.map((r) => r.id))
  }

  function add(): void {
    if (selected.length === 0) return
    // spanRef narrows to the three fields the server keys on -- posting a
    // whole Rally would send fields it ignores today and might not later.
    onadd(selected.map(spanRef))
  }
</script>

<div class="rounded-lg border border-neutral-800 p-4">
  <div class="mb-3 flex items-baseline justify-between">
    <h2 class="text-sm font-semibold">Add rallies</h2>
    <button class="font-mono text-xs text-neutral-400 hover:text-neutral-200" onclick={onclose}>
      close
    </button>
  </div>

  {#if error}
    <p class="rounded bg-red-500/10 p-3 text-sm text-red-300">{error}</p>
  {:else}
    <div class="mb-3 flex flex-wrap items-center gap-3">
      <select
        class="rounded border border-neutral-700 bg-neutral-900 px-2 py-1 text-xs"
        aria-label="Session"
        bind:value={sessionId}
      >
        {#each sessions as s (s.id)}
          <option value={s.id}>{s.title}</option>
        {/each}
      </select>

      {#each ['points', 'starred', 'all'] as f (f)}
        <button
          data-filter={f}
          class="rounded border px-2 py-1 font-mono text-xs
                 {filter === f
                   ? 'border-blue-500 text-blue-300'
                   : 'border-neutral-700 text-neutral-400 hover:bg-neutral-800'}"
          onclick={() => (filter = f as Filter)}
        >{f}</button>
      {/each}

      <button
        data-select-all
        class="ml-auto font-mono text-xs text-neutral-400 hover:text-neutral-200
               disabled:opacity-40"
        disabled={selectable.length === 0}
        onclick={selectAll}
      >select all</button>
    </div>

    {#if loading}
      <p class="text-sm text-neutral-400">Loading…</p>
    {:else if shown.length === 0}
      <p class="text-sm text-neutral-400">Nothing matches this filter in that session.</p>
    {:else}
      <ul class="max-h-72 divide-y divide-neutral-800 overflow-y-auto">
        {#each shown as r (r.id)}
          {@const inReel = alreadyIn.has(spanKey(r))}
          <li>
            <label class="flex items-center gap-3 py-2 text-sm
                          {inReel ? 'text-neutral-500' : ''}">
              <input
                type="checkbox"
                checked={inReel || checked.has(r.id)}
                disabled={inReel}
                onchange={() => toggle(r)}
              />
              <span class="font-mono text-xs tabular-nums">{formatTs(r.start_ms)}</span>
              <span class="font-mono text-xs tabular-nums text-neutral-400">
                {formatDuration(r.end_ms - r.start_ms)}
              </span>
              {#if r.point}<span class="font-mono text-xs text-neutral-400">P</span>{/if}
              {#if r.starred}<span class="font-mono text-xs text-amber-300">★</span>{/if}
              {#if inReel}
                <span class="ml-auto font-mono text-xs text-neutral-600">in reel</span>
              {/if}
            </label>
          </li>
        {/each}
      </ul>
    {/if}

    <button
      data-add
      class="mt-3 rounded border border-neutral-700 px-3 py-1.5 font-mono text-xs
             text-neutral-200 hover:bg-neutral-800 disabled:cursor-not-allowed
             disabled:opacity-40"
      disabled={selected.length === 0}
      onclick={add}
    >
      Add {selected.length} to reel
    </button>
  {/if}
</div>
```

- [ ] **Step 4: Run to verify it passes**

```bash
cd web && npx vitest run tests/add-rallies-picker.test.ts
```

Expected: PASS, 7 tests.

- [ ] **Step 5: Typecheck and full frontend suite**

```bash
cd web && npm run check && npx vitest run
```

Expected: 0 errors, all pass.

- [ ] **Step 6: Commit**

```bash
git add web/src/components/AddRalliesPicker.svelte web/tests/add-rallies-picker.test.ts
git commit -m "feat(web): add-rallies picker for the reel builder"
```

---

### Task 11: The `/reels/:slug` builder

**Files:**
- Modify: `web/src/lib/export.ts` (extract `describeExportCounts`)
- Replace: `web/src/routes/Reel.svelte` (the Task 6 placeholder)
- Test: `web/tests/export.test.ts` (create), `web/tests/reel-builder.test.ts` (create)

**Interfaces:**
- Consumes: `ReelItemList` (Task 8), `ReelPreview` (Task 9), `AddRalliesPicker` (Task 10), `renderBlockedReason` / `spanRef` (Task 7), `createToaster` / `toastToneClasses`, every `api.*Reel*` method (Task 6).
- Produces: `describeExportCounts(result: ExportResult): string`.

- [ ] **Step 1: Extract the count phrasing so it has one implementation**

The builder's *Cut missing clips* and the reviewed panel's *Export point clips* report the same four counts. Two copies would drift, and collapsing the four is precisely the bug the endpoint's contract exists to prevent — so the phrasing moves into one function both call.

Edit `web/src/lib/export.ts`:

```ts
/**
 * The four-count phrase both cut buttons report.
 *
 * `queued` is always shown, even at zero, and the other three only appear
 * when nonzero -- so a second press mid-encode reads as "0 queued, 3 in
 * flight" rather than the false "0 queued, 3 already cut" that the four
 * separate counts exist to prevent (see ExportPlan in splitstep/export.py).
 * One implementation, called from the reviewed panel and from the reel
 * builder: two copies of this would be two chances to collapse the buckets
 * back into one, which is the mistake that once made a second press
 * mid-encode report everything as done.
 */
export function describeExportCounts(result: ExportResult): string {
  const parts = [`${result.queued} queued`]
  if (result.already_cut > 0) parts.push(`${result.already_cut} already cut`)
  if (result.in_flight > 0) parts.push(`${result.in_flight} in flight`)
  if (result.unavailable > 0) parts.push(`${result.unavailable} unavailable`)
  return parts.join(', ')
}

/** A short, human-readable notice for the reviewed panel's toaster. */
export function describeExportResult(which: 'points' | 'starred', result: ExportResult): string {
  return `${exportSetLabel(which)}: ${describeExportCounts(result)}`
}
```

Create `web/tests/export.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { describeExportCounts, describeExportResult, exportSetLabel } from '../src/lib/export'
import type { ExportResult } from '../src/lib/types'

const result = (o: Partial<ExportResult> = {}): ExportResult => ({
  queued: 0, already_cut: 0, in_flight: 0, unavailable: 0, total: 0, ...o,
})

describe('describeExportCounts', () => {
  it('always names queued, even at zero', () => {
    expect(describeExportCounts(result())).toBe('0 queued')
  })

  it('keeps in flight distinct from already cut', () => {
    // The bug this contract exists to prevent: a second press mid-encode
    // reporting everything as done.
    expect(describeExportCounts(result({ in_flight: 3 }))).toBe('0 queued, 3 in flight')
    expect(describeExportCounts(result({ already_cut: 3 }))).toBe('0 queued, 3 already cut')
  })

  it('reports all four when all four are nonzero', () => {
    expect(describeExportCounts(result({
      queued: 1, already_cut: 2, in_flight: 3, unavailable: 4,
    }))).toBe('1 queued, 2 already cut, 3 in flight, 4 unavailable')
  })
})

describe('describeExportResult', () => {
  it('prefixes the set label', () => {
    expect(describeExportResult('points', result({ queued: 2 })))
      .toBe('point clips: 2 queued')
    expect(exportSetLabel('starred')).toBe('starred clips')
  })
})
```

- [ ] **Step 2: Write the failing builder test**

Create `web/tests/reel-builder.test.ts`:

```ts
import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReelDetail, ReelItem } from '../src/lib/types'

function item(start: number, overrides: Partial<ReelItem> = {}): ReelItem {
  return {
    source_id: 'src1',
    session_id: 's1',
    source_idx: 1,
    start_ms: start,
    end_ms: start + 4000,
    duration_ms: 4000,
    position: 0,
    clip_ready: true,
    rally: null,
    ...overrides,
  }
}

function detail(items: ReelItem[], dirty = 1): ReelDetail {
  return {
    reel: {
      id: 'r1', name: '2026-08-18 points', slug: '2026-08-18-points',
      rendered_path: null, rendered_at: null, dirty,
      created_at: '2026-08-21T10:00:00Z', item_count: items.length,
    },
    items,
  }
}

const mockApi = {
  getReel: vi.fn(),
  addReelItems: vi.fn().mockResolvedValue({ added: 1, existing: 0, total: 1 }),
  removeReelItem: vi.fn().mockResolvedValue({ removed: true, total: 0 }),
  setReelOrder: vi.fn().mockResolvedValue({ ok: true }),
  exportReelClips: vi.fn().mockResolvedValue({
    queued: 2, already_cut: 0, in_flight: 0, unavailable: 0, total: 2,
  }),
  renderReel: vi.fn().mockResolvedValue({ job_id: 'j1', already_running: false }),
  listSessions: vi.fn().mockResolvedValue([]),
  getSession: vi.fn().mockResolvedValue({ session: {}, sources: [], rallies: [] }),
  jobs: vi.fn().mockResolvedValue([]),
  frameUrl: () => 'about:blank',
  proxyUrl: () => 'about:blank',
}
vi.mock('../src/lib/api', () => ({ api: mockApi }))

HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
HTMLMediaElement.prototype.pause = vi.fn()
HTMLMediaElement.prototype.load = vi.fn()

const { default: Reel } = await import('../src/routes/Reel.svelte')

let host: HTMLElement
let component: ReturnType<typeof mount> | null = null

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  vi.clearAllMocks()
  mockApi.jobs.mockResolvedValue([])
  mockApi.listSessions.mockResolvedValue([])
})

afterEach(() => {
  if (component) unmount(component)
  component = null
  host.remove()
})

async function open(d: ReelDetail) {
  mockApi.getReel.mockResolvedValue(d)
  component = mount(Reel, { target: host, props: { slug: d.reel.slug } })
  flushSync()
  await Promise.resolve()
  await Promise.resolve()
  flushSync()
}

const render = () => host.querySelector('[data-render]') as HTMLButtonElement
const cut = () => host.querySelector('[data-cut]') as HTMLButtonElement

describe('Reel builder', () => {
  it('renders the reel name and its items', async () => {
    await open(detail([item(1000), item(9000)]))
    expect(host.textContent).toContain('2026-08-18 points')
    expect(host.querySelectorAll('[data-reel-row]')).toHaveLength(2)
  })

  it('disables Render while a clip is missing and names the count', async () => {
    await open(detail([item(1000, { clip_ready: false }), item(9000)]))
    expect(render().disabled).toBe(true)
    // The reason is on the button, so the user knows to press Cut instead.
    // Render must never auto-enqueue the cuts.
    expect(render().textContent).toContain('1 clip not cut yet')
    expect(mockApi.renderReel).not.toHaveBeenCalled()
  })

  it('disables Render on an empty reel', async () => {
    await open(detail([]))
    expect(render().disabled).toBe(true)
  })

  it('enables Render once every clip is ready', async () => {
    await open(detail([item(1000), item(9000)]))
    expect(render().disabled).toBe(false)
    render().click()
    flushSync()
    expect(mockApi.renderReel).toHaveBeenCalledWith('2026-08-18-points')
  })

  it('cutting reports the four counts separately', async () => {
    await open(detail([item(1000, { clip_ready: false })]))
    mockApi.exportReelClips.mockResolvedValue({
      queued: 0, already_cut: 0, in_flight: 3, unavailable: 0, total: 3,
    })
    cut().click()
    flushSync()
    await Promise.resolve()
    await Promise.resolve()
    flushSync()
    expect(host.textContent).toContain('3 in flight')
    expect(host.textContent).not.toContain('already cut')
  })

  it('cutting is the only thing that enqueues an encode', async () => {
    await open(detail([item(1000), item(9000)]))
    render().click()
    flushSync()
    expect(mockApi.exportReelClips).not.toHaveBeenCalled()
  })

  it('persists a reorder and refetches', async () => {
    await open(detail([item(1000), item(9000)]))
    const handles = [...host.querySelectorAll('[data-drag-handle]')] as HTMLElement[]
    handles[0].dispatchEvent(new KeyboardEvent('keydown', {
      bubbles: true, key: 'ArrowDown', altKey: true,
    }))
    flushSync()
    expect(mockApi.setReelOrder).toHaveBeenCalledWith('2026-08-18-points', [
      { source_id: 'src1', start_ms: 9000, end_ms: 13000 },
      { source_id: 'src1', start_ms: 1000, end_ms: 5000 },
    ])
  })

  it('removes an item through the API', async () => {
    await open(detail([item(1000)]))
    ;(host.querySelector('[data-remove]') as HTMLElement).click()
    flushSync()
    expect(mockApi.removeReelItem).toHaveBeenCalledWith('2026-08-18-points', {
      source_id: 'src1', start_ms: 1000, end_ms: 5000,
    })
  })

  it('surfaces a failed reorder instead of leaving a phantom order', async () => {
    // 409 means the client's membership view is stale. Refetching is the
    // fix, and saying so beats a list that silently disagrees with the
    // server about what order it is in.
    await open(detail([item(1000), item(9000)]))
    mockApi.setReelOrder.mockRejectedValue(new Error('409 stale'))
    const handles = [...host.querySelectorAll('[data-drag-handle]')] as HTMLElement[]
    handles[0].dispatchEvent(new KeyboardEvent('keydown', {
      bubbles: true, key: 'ArrowDown', altKey: true,
    }))
    flushSync()
    await Promise.resolve()
    await Promise.resolve()
    flushSync()
    expect(host.textContent).toContain("Couldn't reorder")
  })
})
```

- [ ] **Step 3: Run to verify they fail**

```bash
cd web && npx vitest run tests/export.test.ts tests/reel-builder.test.ts
```

Expected: `export.test.ts` fails on the missing `describeExportCounts`; `reel-builder.test.ts` fails because the placeholder route renders nothing.

- [ ] **Step 4: Write `web/src/routes/Reel.svelte`**, replacing the placeholder

```svelte
<script lang="ts">
  import AddRalliesPicker from '../components/AddRalliesPicker.svelte'
  import JobsBadge from '../components/JobsBadge.svelte'
  import ReelItemList from '../components/ReelItemList.svelte'
  import ReelPreview from '../components/ReelPreview.svelte'
  import { api } from '../lib/api'
  import { describeExportCounts } from '../lib/export'
  import { renderBlockedReason, spanRef } from '../lib/reels'
  import { navigate } from '../lib/router.svelte'
  import { createToaster, toastToneClasses } from '../lib/toaster.svelte'
  import type { ReelDetail, ReelItem, SpanRef } from '../lib/types'

  let { slug }: { slug: string } = $props()

  let detail = $state<ReelDetail | null>(null)
  let error = $state<string | null>(null)
  let loading = $state(true)
  let showPicker = $state(false)
  let showPreview = $state(false)
  let busy = $state(false)
  const toaster = createToaster()

  // Bumped after every successful mutation to force a refetch. The server is
  // the source of truth for clip_ready and for orphan status -- both can
  // change under us (an encode finishing, a re-segment in another tab) --
  // so the list is re-read rather than patched locally.
  let revision = $state(0)

  $effect(() => {
    const currentSlug = slug
    revision
    let cancelled = false
    loading = true
    error = null
    api
      .getReel(currentSlug)
      .then((d) => {
        if (!cancelled) detail = d
      })
      .catch((e) => {
        if (!cancelled) error = String(e)
      })
      .finally(() => {
        if (!cancelled) loading = false
      })
    return () => {
      cancelled = true
    }
  })

  const items = $derived(detail?.items ?? [])
  const blocked = $derived(renderBlockedReason(items))
  const existing = $derived<SpanRef[]>(items.map(spanRef))

  async function mutate(fn: () => Promise<unknown>, failure: string): Promise<void> {
    // One in-flight mutation at a time. Every one of these rewrites
    // membership or order and then refetches; overlapping them would let an
    // older response land after a newer one and render a state the user has
    // already moved past -- the same hazard LabelWriter serialises against.
    if (busy) return
    busy = true
    try {
      await fn()
      revision += 1
    } catch (e) {
      toaster.push(`${failure} -- ${String(e)}`)
      // Refetch anyway: a rejected reorder means our view of the membership
      // is stale, and leaving the stale list on screen is what makes the
      // next drag fail too.
      revision += 1
    } finally {
      busy = false
    }
  }

  function commitOrder(next: ReelItem[]): void {
    mutate(() => api.setReelOrder(slug, next.map(spanRef)), "Couldn't reorder")
  }

  function remove(item: ReelItem): void {
    mutate(() => api.removeReelItem(slug, spanRef(item)), "Couldn't remove that clip")
  }

  function addSpans(spans: SpanRef[]): void {
    showPicker = false
    mutate(async () => {
      const result = await api.addReelItems(slug, spans)
      toaster.push(
        `${result.added} added${result.existing ? `, ${result.existing} already in` : ''}`,
        'info',
      )
    }, "Couldn't add those rallies")
  }

  async function cutMissing(): Promise<void> {
    // Fire-and-forget, exactly like the reviewed panel's export: encode
    // progress is the jobs badge's job, and a second progress UI here would
    // be a second thing to keep correct. The four counts stay four.
    try {
      const result = await api.exportReelClips(slug)
      toaster.push(`Clips: ${describeExportCounts(result)}`, 'info')
      revision += 1
    } catch (e) {
      toaster.push(`Couldn't cut clips -- ${String(e)}`)
    }
  }

  async function render(): Promise<void> {
    // Guarded by `blocked` on the button too; repeated here because the
    // button is not the only way this can be reached once a clip is deleted
    // between the fetch and the click.
    if (blocked) return
    try {
      const result = await api.renderReel(slug)
      toaster.push(
        result.already_running ? 'Already rendering.' : 'Rendering — see the jobs badge.',
        'info',
      )
      revision += 1
    } catch (e) {
      toaster.push(`Couldn't render -- ${String(e)}`)
    }
  }
</script>

<header class="mb-6 flex items-baseline justify-between">
  <div>
    <button class="font-mono text-xs text-neutral-400 hover:text-neutral-200"
            onclick={() => navigate('/reels')}>← Reels</button>
    <h1 class="mt-1 text-xl font-semibold">{detail?.reel.name ?? slug}</h1>
  </div>
  <JobsBadge />
</header>

{#if error}
  <p class="rounded bg-red-500/10 p-3 text-sm text-red-300">{error}</p>
{:else if loading && !detail}
  <p class="text-sm text-neutral-400">Loading…</p>
{:else if detail}
  <div class="mb-4 flex flex-wrap items-center gap-3">
    <button
      class="rounded border border-neutral-700 px-3 py-1.5 font-mono text-xs text-neutral-200
             hover:bg-neutral-800"
      onclick={() => (showPicker = !showPicker)}
    >Add rallies</button>

    <button
      class="rounded border border-neutral-700 px-3 py-1.5 font-mono text-xs text-neutral-200
             hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-40"
      disabled={items.length === 0}
      onclick={() => (showPreview = !showPreview)}
    >{showPreview ? 'Hide preview' : 'Preview'}</button>

    <!-- Cutting is the ONLY action here that starts an encode. Render never
         enqueues clips: a button labelled "render" must not silently launch
         half an hour of work. -->
    <button
      data-cut
      class="rounded border border-neutral-700 px-3 py-1.5 font-mono text-xs text-neutral-200
             hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-40"
      disabled={items.length === 0}
      onclick={cutMissing}
    >Cut missing clips</button>

    <button
      data-render
      class="rounded border border-neutral-700 px-3 py-1.5 font-mono text-xs text-neutral-200
             hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-40"
      disabled={blocked !== null}
      title={blocked ?? ''}
      onclick={render}
    >{blocked ? `Render — ${blocked}` : 'Render'}</button>

    <span class="ml-auto font-mono text-xs text-neutral-500">
      {items.length} clips{detail.reel.rendered_path && !detail.reel.dirty
        ? ` · ${detail.reel.rendered_path}`
        : ''}
    </span>
  </div>

  {#if showPicker}
    <div class="mb-4">
      <AddRalliesPicker
        {existing}
        defaultSessionId={items.at(-1)?.session_id ?? null}
        onadd={addSpans}
        onclose={() => (showPicker = false)}
      />
    </div>
  {/if}

  {#if showPreview && items.length > 0}
    <!-- Keyed on the membership so a change remounts the preview with a
         fresh controller rather than mutating one mid-playback, the same
         guarantee Session.svelte gives QueueMode. -->
    <div class="mb-4">
      {#key items}
        <ReelPreview {items} onclose={() => (showPreview = false)} />
      {/key}
    </div>
  {/if}

  {#if items.length === 0}
    <p class="text-sm text-neutral-400">
      Nothing in this reel yet. Add rallies above, or compile a session's points from the
      end of its review queue.
    </p>
  {:else}
    <ReelItemList {items} oncommit={commitOrder} onremove={remove} />
  {/if}
{/if}

{#if toaster.toasts.length > 0}
  <div class="pointer-events-none fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 flex-col
              items-center gap-2">
    {#each toaster.toasts as t (t.id)}
      <p class="rounded-full px-4 py-2 font-mono text-xs {toastToneClasses(t.tone)}">
        {t.message}
      </p>
    {/each}
  </div>
{/if}
```

- [ ] **Step 5: Run to verify they pass**

```bash
cd web && npx vitest run tests/export.test.ts tests/reel-builder.test.ts
```

Expected: PASS.

- [ ] **Step 6: Typecheck and full frontend suite**

```bash
cd web && npm run check && npx vitest run
```

Expected: 0 errors, all pass — including `export-buttons.test.ts`, which exercises `describeExportResult` through the reviewed panel and must be unaffected by the extraction.

- [ ] **Step 7: Commit**

```bash
git add web/src/routes/Reel.svelte web/src/lib/export.ts web/tests/export.test.ts web/tests/reel-builder.test.ts
git commit -m "feat(web): the reel builder"
```

---

### Task 12: The reviewed panel's two reel buttons

**Files:**
- Modify: `web/src/components/QueueMode.svelte`
- Test: `web/tests/reel-buttons.test.ts` (create)

**These are additional buttons, not replacements.** Plan A's *Export point clips (N)* and *Export starred clips (N)* stay exactly as they are; the panel ends up with four actions in two rows — cut, and compile. Creating a reel cuts nothing.

- [ ] **Step 1: Write the failing test**

Create `web/tests/reel-buttons.test.ts`. It mirrors `export-buttons.test.ts` — same mock module, same `SessionHarness` — with `createSessionReel` added to the mock and `navigate` spied on:

```ts
import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Rally, SessionDetail } from '../src/lib/types'

const mockApi = {
  listSessions: vi.fn(),
  getSession: vi.fn(),
  star: vi.fn().mockResolvedValue({ ok: true }),
  reject: vi.fn().mockResolvedValue({ ok: true }),
  point: vi.fn().mockResolvedValue({ ok: true }),
  reviewed: vi.fn().mockResolvedValue({ ok: true }),
  setBounds: vi.fn().mockResolvedValue({ ok: true }),
  resegment: vi.fn(),
  label: vi.fn().mockResolvedValue({ ok: true }),
  sourceLabels: vi.fn().mockResolvedValue([]),
  scores: vi.fn().mockResolvedValue({ step_ms: 200, threshold: 0.45, scores: [] }),
  listPresets: vi.fn().mockResolvedValue([]),
  createPreset: vi.fn().mockResolvedValue({ id: 'preset1' }),
  setPreset: vi.fn().mockResolvedValue({ ok: true }),
  jobs: vi.fn().mockResolvedValue([]),
  proxyUrl: () => 'about:blank',
  frameUrl: () => 'about:blank',
  getSource: vi.fn(),
  setup: vi.fn(),
  previewUrl: () => 'about:blank',
  exportClips: vi.fn().mockResolvedValue({
    queued: 2, already_cut: 0, in_flight: 0, unavailable: 0, total: 2,
  }),
  createSessionReel: vi.fn(),
}
vi.mock('../src/lib/api', () => ({ api: mockApi }))

const navigate = vi.fn()
vi.mock('../src/lib/router.svelte', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  navigate,
}))

HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
HTMLMediaElement.prototype.pause = vi.fn()
HTMLMediaElement.prototype.load = vi.fn()

const { default: SessionHarness } = await import('./support/SessionHarness.svelte')

function rally(id: string, idx: number, overrides: Partial<Rally> = {}): Rally {
  return {
    id, session_id: 's1', source_id: 'src1', idx,
    start_ms: idx * 10000, end_ms: idx * 10000 + 8000,
    det_start_ms: idx * 10000, det_end_ms: idx * 10000 + 8000,
    confidence: 0.9, starred: 0, rejected: 0, point: 0, reviewed_at: null,
    ...overrides,
  }
}

function reviewed(): SessionDetail {
  return {
    session: { id: 's1', title: 'test session', played_on: '2026-08-19', status: 'ready' },
    sources: [{
      id: 'src1', session_id: 's1', idx: 1, recorded_at: '2026-08-19T10:00:00Z',
      offset_ms: 0, duration_ms: 600000, width: 1920, height: 1080, fps: 30,
      has_original: 1, court_preset_id: null, status: 'ready', rotation_deg: 0,
    }],
    rallies: [
      rally('r1', 1, { point: 1, reviewed_at: '2026-08-19T11:00:00Z' }),
      rally('r2', 2, { point: 1, reviewed_at: '2026-08-19T11:01:00Z' }),
      rally('r3', 3, { starred: 1, reviewed_at: '2026-08-19T11:02:00Z' }),
    ],
  }
}

let target: HTMLDivElement
let instance: unknown

beforeEach(() => {
  vi.clearAllMocks()
  mockApi.jobs.mockResolvedValue([])
  mockApi.createSessionReel.mockResolvedValue({
    slug: '2026-08-19-points', name: '2026-08-19 points',
    added: 2, existing: 0, total: 2,
  })
  target = document.createElement('div')
  document.body.appendChild(target)
})

afterEach(() => {
  if (instance) unmount(instance as never)
  target.remove()
  instance = undefined
})

function button(label: RegExp): HTMLButtonElement {
  const el = Array.from(target.querySelectorAll('button')).find((b) =>
    label.test(b.textContent ?? ''),
  )
  if (!el) throw new Error(`no button matching ${label}`)
  return el as HTMLButtonElement
}

async function openReviewed() {
  mockApi.getSession.mockResolvedValue(reviewed())
  instance = mount(SessionHarness, { target })
  flushSync()
  await vi.waitFor(() => expect(target.textContent).toMatch(/Session reviewed/))
}

describe('the reviewed panel compiles reels', () => {
  it('keeps both export buttons alongside both reel buttons', async () => {
    // §6.2: additions, not replacements. Four actions in two rows.
    await openReviewed()
    expect(button(/Export point clips/i)).toBeTruthy()
    expect(button(/Export starred clips/i)).toBeTruthy()
    expect(button(/Reel of all points/i).textContent).toMatch(/2/)
    expect(button(/Reel of starred/i).textContent).toMatch(/1/)
  })

  it('creates the reel for the set the button names and opens the builder', async () => {
    await openReviewed()
    button(/Reel of all points/i).click()
    await vi.waitFor(() => expect(mockApi.createSessionReel).toHaveBeenCalled())
    expect(mockApi.createSessionReel).toHaveBeenCalledWith('s1', 'points')
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith('/reels/2026-08-19-points'))
  })

  it('creating a reel cuts nothing', async () => {
    // The line §6.2 draws: a button labelled "reel" must never start half an
    // hour of encoding. Cutting stays the export buttons' job.
    await openReviewed()
    button(/Reel of starred/i).click()
    await vi.waitFor(() => expect(mockApi.createSessionReel).toHaveBeenCalled())
    expect(mockApi.exportClips).not.toHaveBeenCalled()
  })

  it('disables a reel button whose set is empty', async () => {
    mockApi.getSession.mockResolvedValue({
      ...reviewed(),
      rallies: [rally('r1', 1, { point: 1, reviewed_at: '2026-08-19T11:00:00Z' })],
    })
    instance = mount(SessionHarness, { target })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Session reviewed/))
    expect(button(/Reel of starred/i).disabled).toBe(true)
  })

  it('surfaces a failure instead of navigating', async () => {
    await openReviewed()
    mockApi.createSessionReel.mockRejectedValue(new Error('boom'))
    button(/Reel of all points/i).click()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Couldn't build/))
    expect(navigate).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd web && npx vitest run tests/reel-buttons.test.ts
```

Expected: FAIL — `no button matching /Reel of all points/i`.

- [ ] **Step 3: Extend `web/src/components/QueueMode.svelte`**

Add to the imports:

```ts
  import { navigate } from '../lib/router.svelte'
```

Add beside `exportSet`:

```ts
  // Creating a reel cuts NOTHING. §6.2 draws that line deliberately: the
  // builder's "Cut missing clips" stays the only path that starts an
  // encode, so a button labelled "reel" never silently launches half an
  // hour of work. A second press merges additively server-side, so pressing
  // this again after marking three more points appends those three and
  // leaves any hand-ordering alone.
  async function buildReel(which: 'points' | 'starred'): Promise<void> {
    try {
      const result = await api.createSessionReel(detail.session.id, which)
      navigate(`/reels/${result.slug}`)
    } catch (e) {
      toaster.push(`Couldn't build the ${exportSetLabel(which)} reel -- ${String(e)}`)
    }
  }
```

Replace the reviewed panel's single button row with two rows, keeping the existing pair untouched:

```svelte
    <!-- Two rows, four actions: cut, and compile. The reel buttons are
         ADDITIONS beside Plan A's export pair, never replacements -- cutting
         clips and compiling a reel are different decisions, and only the
         first one starts an encode. -->
    <div class="mt-4 flex items-center justify-center gap-3">
      <button
        class="rounded border border-neutral-700 px-3 py-1.5 font-mono text-xs text-neutral-200
               hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-40"
        disabled={stats.pointCount === 0}
        onclick={() => exportSet('points')}
      >
        Export point clips ({stats.pointCount})
      </button>
      <button
        class="rounded border border-neutral-700 px-3 py-1.5 font-mono text-xs text-neutral-200
               hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-40"
        disabled={stats.starredCount === 0}
        onclick={() => exportSet('starred')}
      >
        Export starred clips ({stats.starredCount})
      </button>
    </div>
    <div class="mt-2 flex items-center justify-center gap-3">
      <button
        class="rounded border border-neutral-700 px-3 py-1.5 font-mono text-xs text-neutral-200
               hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-40"
        disabled={stats.pointCount === 0}
        onclick={() => buildReel('points')}
      >
        Reel of all points ({stats.pointCount})
      </button>
      <button
        class="rounded border border-neutral-700 px-3 py-1.5 font-mono text-xs text-neutral-200
               hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-40"
        disabled={stats.starredCount === 0}
        onclick={() => buildReel('starred')}
      >
        Reel of starred ({stats.starredCount})
      </button>
    </div>
```

Also drop the now-stale comment above the first row — it reads "Cutting only -- reel creation is a separate plan (Plan B)", and Plan B is this.

- [ ] **Step 4: Run to verify it passes**

```bash
cd web && npx vitest run tests/reel-buttons.test.ts
```

Expected: PASS, 5 tests.

- [ ] **Step 5: Typecheck and full frontend suite**

```bash
cd web && npm run check && npx vitest run
```

Expected: 0 errors, all pass — `export-buttons.test.ts` in particular, which asserts the two export buttons still work.

- [ ] **Step 6: Commit**

```bash
git add web/src/components/QueueMode.svelte web/tests/reel-buttons.test.ts
git commit -m "feat(web): compile a session's points or stars into a reel"
```

---

### Task 13: Documentation and end-to-end verification

**Files:**
- Modify: `CLAUDE.md`
- Test: the whole suite, both languages

- [ ] **Step 1: Update `CLAUDE.md`'s Deferred section**

At the time of writing it reads:

```
Reel building via `-c copy` concat and the cross-session rally browser are
Plan 3. The `reels`/`reel_items` tables exist unused. 4K clip export shipped —
`splitstep clips export`, the `clip` handler, and `clips_dir` are live.
```

Replace the first two sentences (leave the Reclaim Space paragraph and the clip-export sentence exactly as they are):

```
The cross-session rally browser is the only piece of Plan 3 still deferred —
it is a filter UI over one session's rallies until a second session exists.
Reels shipped: `reel_items` is keyed on `(source_id, start_ms, end_ms)` by
migration 006, the `reel` handler concatenates with `-c copy`, and `/reels`
plus `/reels/:slug` build and preview them. 4K clip export shipped —
`splitstep clips export`, the `clip` handler, and `clips_dir` are live.
```

- [ ] **Step 2: Add a Reels section to `CLAUDE.md`'s Architecture**

Insert after the "Jobs" section (before "API"):

```markdown
### Reels

A reel is an ordered list of **clips**, and `reel_items` keys on
`(source_id, start_ms, end_ms)` — the same triple `clip_relpath()` names the
file for. Never on `rally_id`: `001_init.sql` declared it that way with
`ON DELETE CASCADE`, and `replace_rallies` deletes every rally for a source
on each sweep, so the first re-segment would have silently emptied every
reel. Migration `006` replaced the table before it ever held a row. The
cascade on `source_id` is deliberate and is the opposite case — no footage,
no clip.

An item whose span no rally holds any more is an **orphan**. It is badged in
the builder and stays playable, cuttable and renderable; `handle_clip`
therefore treats `rally_id` as optional. A reel is session-agnostic, so
`resolve_items` joins `session_id` and `source_idx` in — the preview's proxy
URL and the clip path both need them.

The `reel` job concatenates with `-c copy`, then **probes the output and
compares its duration against the sum of the inputs**, falling back to a full
re-encode on a mismatch and logging it. A silent `-c copy` failure is a known
ffmpeg trap and a reel quietly missing its last four points is the failure
worth thirty extra seconds. Render **refuses** while any clip is missing,
naming the count, and never auto-enqueues the cuts: the builder's *Cut
missing clips* is the only button that starts an encode.

Preview seeks the **proxy** to each item's span in order, reusing `VideoDeck`
— it already plays a source between in/out points and preloads the next span
across sources. It shows 1080p and cannot reveal a `-c copy` artifact (that
is the duration probe's job); what it shows exactly is timing.
```

- [ ] **Step 3: Run the whole Python suite and lint**

```bash
~/miniconda3/envs/splitstep/bin/pytest -q && ~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
```

Expected: all green. Record the test count — Step 5 updates `CLAUDE.md` with it.

- [ ] **Step 4: Run the whole frontend suite, typecheck, and build**

```bash
cd web && npx vitest run && npm run check && npm run build
```

Expected: all pass, 0 svelte-check errors, `web/dist` written. The build matters: `splitstep serve` mounts `web/dist`, so a route that only works under `npm run dev` is not shipped.

- [ ] **Step 5: Update the test count in `CLAUDE.md`**

```
~/miniconda3/envs/splitstep/bin/pytest -q                              # NNN tests
```

Use the number Step 3 printed. The baseline before Plan B is **511 pytest / 326 vitest** at `3e924c8`; if Step 3 prints fewer than 511, something was deleted — stop and find out what.

- [ ] **Step 6: Verify the migration applies to a fresh library**

```bash
~/miniconda3/envs/splitstep/bin/python -c "
import sqlite3, tempfile, pathlib
from splitstep.db.schema import connect, migrate
p = pathlib.Path(tempfile.mkdtemp()) / 'library.db'
c = connect(p); migrate(c)
print('user_version', c.execute('PRAGMA user_version').fetchone()[0])
print('fks', [dict(r) for r in c.execute('PRAGMA foreign_key_list(reel_items)')])
print('cols', [r['name'] for r in c.execute('PRAGMA table_info(reel_items)')])
"
```

Expected: `user_version 6`, foreign keys naming only `reels` and `sources`, and no `rally_id` column.

**Do not run this against the user's real library, and do not start or restart the server.** One is running on port 8420 against a library holding 61 rallies, 24 points and 24 cut clips. Migration `006` will apply on that server's next restart, which is the user's call to make.

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: reels shipped"
```

- [ ] **Step 8: Hand back**

Report to the user:
- the Python and frontend test counts,
- that migration `006` has **not** been applied to their real library and will run on the next `splitstep serve` restart,
- that `npm run build` has been run, so `splitstep serve` will pick up the new routes on that same restart,
- anything a real 24-item reel would exercise that the tests do not: the actual `-c copy` of 24 locked-profile 4K clips, and how long the concat takes on the real drive.

---

## Self-review

**Spec coverage**

| Spec | Task |
|---|---|
| §5 `006_reel_items_by_span.sql`, no rally FK | 1 |
| §5 orphan resolves as orphaned, still playable/renderable | 3 (`resolve_items`), 8 (badge), 9 (preview does not filter) |
| §5.1 `-c copy` into `reels/<slug>.mp4` | 2, 4 |
| §5.1 probe the output, compare to the sum, re-encode fallback, log it | 2, 4 |
| §5.1 render refuses on missing clips, naming the count | 4 (handler), 5 (409), 11 (button) |
| §5.1 `dirty` set on membership/order change, cleared on render | 1, 5, 4 |
| §6.1 `P` in queue mode | shipped in Plan A |
| §6.2 two reel buttons, additive to the export pair | 12 |
| §6.2 `<date> <set>` name, slug with `-2` suffix on collision | 1 (`unique_slug`), 5 |
| §6.2 second click merges additively, order untouched | 1 (`add_items`), 5, tested both |
| §6.3 `/reels` list with counts and state, New reel | 6 |
| §6.3 `/reels/:slug` builder | 11 |
| §6.4 thumbnail, duration, source, ready/missing badge | 8 |
| §6.4 ready is exact-path only, never a bare glob | 3 (test), 8 |
| §6.4 drag to reorder, math in `lib/reorder.ts`, no dependency | 7, 8 |
| §6.4 remove | 8, 11 |
| §6.4 add-rallies picker, points/starred/all | 10 |
| §6.4 cut missing reuses `ExportPlan`, four outcomes stay four | 3, 5, 11 |
| §6.4 progress via the jobs badge, no second progress UI | 11 |
| §6.4 render disabled with the count on the button | 7, 11 |
| §6.5 preview seeks the proxy, reuses `VideoDeck`, pure controller | 9 |
| §7 free space checked before `reel` | 4 |
| §7 ffmpeg stderr into `jobs.error`, job marked failed | existing `run_ffmpeg` + `Worker` |
| §8 concat test: three clips, sum, plus the fallback path | 2 |
| §8 migration 006 recreates without a rally FK | 1 |
| §8 frontend pure logic: `moveItem`, pointer-y → index, preview controller, additive merge | 7, 9 |

§8's clip-profile, rotation and `replace_rallies`-carries-`point` tests, and §8's span-derived clip path test, all shipped with Plan A.

**Not covered, deliberately:** §9's out-of-scope list (cross-session browser, Reclaim Space — now rejected outright in `CLAUDE.md` — per-reel trim overrides, music/titles/transitions, vertical export, chaining clip files). A `splitstep reels` CLI: §6 is UI-only and every operation has a route.

**Known limits to state rather than discover**

- Two tabs on one reel are unordered. The order route 409s on a stale membership, which surfaces the collision, but a genuine last-writer-wins reorder race would need a server-side revision — the same limit `LabelWriter` documents for two tabs on one rally.
- `tolerance_ms` is calibrated for the failure that matters (a reel short by whole clips), not for frame-exact accounting. A concat that loses a single frame passes.
- The concat tests use 320x240 inputs, not the locked 4K profile: they verify the mechanism, and the profile itself is asserted by Plan A's `ffprobe` test on `make_clip`. The first real 24-clip render is the thing no test covers — see Task 13 Step 8.

**Revision, 2026-08-21 (after Plan A's whole-branch follow-up merged at `3e924c8`)**

- Task 2 rewritten. The spec's §4.2 correction — the concat demuxer exits 0 on mismatched parameters and reads every input through the first clip's — means the duration check specified in §5.1 is blind to two of the three ways a copy goes wrong. A pre-flight comparison of the inputs was added *beside* it, not in place of it. §5.1's check is unchanged.
- Task 2 also extracts `ffprobe_json` from `probe.py`, so concat reads stream-level parameters through the one existing ffprobe implementation rather than a second one.
- Task 4's `handle_reel` takes the third `progress: ProgressFn = no_progress` argument the `Handler` type now requires, and threads it to `concat_clips`' re-encode fallback.
- Baselines corrected to 511 pytest / 326 vitest.

Coverage added by the revision: **spec §4.2's measured concat behaviour** → Task 2 (`ClipParams`, `divergences`, and the two tests that assert a mismatched SAR and a missing audio stream each skip the copy entirely).

Limit worth stating: `divergences` compares inputs to **each other**, not to the locked profile's constants. A library cut uniformly at some other profile concatenates correctly and is allowed to. What it cannot catch is a reel whose every clip is uniformly wrong — that is `make_clip`'s job, asserted by Plan A's `ffprobe` profile test.
