# Numbered Reels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reel can render with a burned-in "3/20" counter and an optional per-item note beneath it, entered in the builder and shown in the preview — Phase 4 of the Mac-app distribution spec.

**Architecture:** `note` column on `reel_items` (migration 012); a note route; a `numbered` flag on the render request that rides the reel job's payload; a per-clip `drawtext` re-encode into temp intermediates at the library's locked colour profile, concatenated by the existing guarded `-c copy` pipeline; builder note input, render toggle, and preview badge in the Svelte app. Spec: `docs/superpowers/specs/2026-08-26-mac-app-distribution-design.md` (Phase 4 section).

**Branch:** `phase1-server-friend-readiness`, checkout `/Users/stevenkhaw/Documents/GitHub/SplitStep-phase1-test`.

## Global Constraints

- Interpreter: `~/miniconda3/envs/splitstep/bin/python -m pytest …` — `-m` form REQUIRED in this worktree. Full suite ONE FOREGROUND call, timeout 600000ms; no backgrounding/polling/watchers.
- `~/miniconda3/envs/splitstep/bin/ruff check splitstep tests` clean before every commit (I001, BLE001, PLW1510 active; line length 100).
- pytest `filterwarnings = ["error"]`. YOLO never runs in tests; ffmpeg does.
- Web: `cd web && npm install` first (worktree has no node_modules); `npx vitest run` for lib tests; `npm run check` (svelte-check) and `npm run build` must pass. All logic in `web/src/lib/` (pure TS, tested); components stay thin shells. Design tokens only — no raw palette steps; `font-data` for every count and timecode.
- Migrations: NEW file `012_…`; never edit an applied one. Current highest on this branch: `011_settings.sql`.
- Comments explain why, not what; extend rationale, never strip. Commit style: lowercase `type(scope): imperative summary`.
- Canonical colour-profile tuple order: `(range, space, trc, primaries)` — same as `HLG_PROFILE`.
- Baseline on the branch: 783 passed, ruff clean, at 5718dbe.

---

### Task 1: `note` on reel items — migration 012, db layer, resolve, API shape

**Files:**
- Create: `splitstep/db/migrations/012_reel_item_notes.sql`
- Modify: `splitstep/db/reels.py` (add `ITEM_NOTE_MAX_CHARS`, `set_item_note`), `splitstep/reels.py` (`ReelItem.note`, `resolve_items`), `splitstep/api/routes.py` (`_item_json` — find it by name; it serializes ReelItem for the reel detail route)
- Test: `tests/test_db_reels.py`, `tests/test_api_reels.py`, `tests/test_db.py` (migration count 11→12 — the same mechanical bump Tasks 6/10 of Phase 1 did)

**Interfaces:**
- Produces: `reel_items.note TEXT NOT NULL DEFAULT ''`; `ITEM_NOTE_MAX_CHARS = 40`; `set_item_note(conn, reel_id, source_id, start_ms, end_ms, note) -> bool` (False when no such item); `ReelItem.note: str`; reel detail JSON items carry `"note"`. Tasks 2-4 consume all of these.

- [ ] **Step 1: Migration** — `splitstep/db/migrations/012_reel_item_notes.sql`:

```sql
-- A short caption per reel item ("match point"), burned beneath the "3/20"
-- counter in a numbered render and shown in the preview. On reel_items, not
-- on the clip: clips are shared across reels (same span can be #3 in one
-- reel and #11 in another) so the note belongs to the membership row, which
-- already survives re-segments by being keyed on the span.
ALTER TABLE reel_items ADD COLUMN note TEXT NOT NULL DEFAULT '';
```

- [ ] **Step 2: Failing tests.** `tests/test_db_reels.py` (follow the file's existing fixture style for creating a reel + items — it has helpers already):

```python
def test_set_item_note_round_trips(conn, ...):
    # create reel + one item via the file's existing helpers
    assert set_item_note(conn, reel_id, source_id, start_ms, end_ms, "match point") is True
    row = list_items(conn, reel_id)[0]
    assert row["note"] == "match point"


def test_set_item_note_on_a_missing_item_is_false(conn, ...):
    assert set_item_note(conn, reel_id, "nope", 1, 2, "x") is False
```

`tests/test_api_reels.py`: the reel detail route's items each carry `"note": ""` by default (extend an existing detail-shape test rather than duplicating its setup). `tests/test_db.py`: bump the `migrate(conn) == 11` literals to `== 12` (grep for them) and add `"reel item note"`-style coverage only if the file's conventions expect it.

- [ ] **Step 3: Implement.** `splitstep/db/reels.py`:

```python
# Short enough to burn legibly beneath the counter at 4K and to fit the
# builder's inline field -- the same trim-then-measure rule rally notes use.
ITEM_NOTE_MAX_CHARS = 40


def set_item_note(
    conn: sqlite3.Connection, reel_id: str, source_id: str,
    start_ms: int, end_ms: int, note: str,
) -> bool:
    """Set one item's note, keyed by the span like every reel-item write.
    False when the reel has no such item -- the caller turns that into a 404
    rather than this function guessing."""
    cur = conn.execute(
        "UPDATE reel_items SET note = ? WHERE reel_id = ? AND source_id = ?"
        " AND start_ms = ? AND end_ms = ?",
        (note, reel_id, source_id, start_ms, end_ms),
    )
    conn.commit()
    return cur.rowcount == 1
```

`splitstep/reels.py`: add `note: str` to the `ReelItem` dataclass and `note=row["note"]` in `resolve_items`. `splitstep/api/routes.py` `_item_json`: add `"note": item.note`.

- [ ] **Step 4: Run** `tests/test_db_reels.py tests/test_api_reels.py tests/test_db.py tests/test_reels.py -q` → PASS (test_reels.py constructs ReelItem directly in places — if its constructions fail on the new field, add `note=""` there; report each). Ruff clean.

- [ ] **Step 5: Commit** — `feat(reels): a per-item note, keyed on the span like the item itself`

---

### Task 2: Note route + `numbered` render flag

**Files:**
- Modify: `splitstep/api/routes.py` (note route after `api_set_reel_order`; `api_render_reel` body), `splitstep/db/jobs.py` (`enqueue_reel_once` payload)
- Test: `tests/test_api_reels.py`, `tests/test_jobs.py`

**Interfaces:**
- Consumes: `set_item_note`, `ITEM_NOTE_MAX_CHARS` (Task 1).
- Produces: `POST /api/reels/{slug}/items/note` body `{source_id, start_ms, end_ms, note}` → `{"ok": true}`, 404 unknown reel/item, 422 over-long note; `POST /api/reels/{slug}/render` optional body `{"numbered": true}` → reel job payload `{"reel_id": …, "numbered": bool}`; `enqueue_reel_once(conn, reel_id, numbered: bool = False)`. Task 3 reads `payload["numbered"]`; Task 4 calls both routes.

- [ ] **Step 1: Failing tests.** `tests/test_api_reels.py`:

```python
def test_item_note_sets_and_survives_the_detail_fetch(client, ...):
    # existing reel+item fixture; then:
    r = client.post(f"/api/reels/{slug}/items/note", json={
        "source_id": sid, "start_ms": a, "end_ms": b, "note": "match point"})
    assert r.status_code == 200
    detail = client.get(f"/api/reels/{slug}").json()
    assert detail["items"][0]["note"] == "match point"


def test_item_note_404s_a_span_not_in_the_reel(client, ...):
    assert client.post(f"/api/reels/{slug}/items/note", json={
        "source_id": sid, "start_ms": 1, "end_ms": 2, "note": "x"}).status_code == 404


def test_item_note_refuses_an_over_long_note(client, ...):
    assert client.post(f"/api/reels/{slug}/items/note", json={
        "source_id": sid, "start_ms": a, "end_ms": b, "note": "x" * 41}).status_code == 422


def test_render_carries_the_numbered_flag_into_the_job(client, conn, ...):
    # reel with all clips cut (reuse the existing render-route fixture):
    client.post(f"/api/reels/{slug}/render", json={"numbered": True})
    row = conn.execute("SELECT payload FROM jobs WHERE type='reel'").fetchone()
    assert json.loads(row["payload"]) == {"reel_id": reel_id, "numbered": True}


def test_render_without_a_body_stays_plain(client, conn, ...):
    client.post(f"/api/reels/{slug}/render")
    row = conn.execute("SELECT payload FROM jobs WHERE type='reel'").fetchone()
    assert json.loads(row["payload"])["numbered"] is False
```

`tests/test_jobs.py`: `enqueue_reel_once(conn, "r1", numbered=True)` writes the flag; a second call (any flag) returns the in-flight job — one render at a time regardless of kind, because both write the same output file.

- [ ] **Step 2: Verify failures.**

- [ ] **Step 3: Implement.** Models (near the other reel bodies in routes.py):

```python
class ItemNoteBody(BaseModel):
    source_id: str
    start_ms: int
    end_ms: int
    note: str

    @field_validator("note")
    @classmethod
    def check_note(cls, v: str) -> str:
        # Trim before measuring and store what was measured -- the same rule
        # NoteBody applies to rally notes, for the same reviewer-facing reason.
        v = v.strip()
        if len(v) > ITEM_NOTE_MAX_CHARS:
            raise ValueError(f"a note is at most {ITEM_NOTE_MAX_CHARS} characters")
        return v


class RenderBody(BaseModel):
    numbered: bool = False
```

Route (after `api_set_reel_order`; import `set_item_note`, `ITEM_NOTE_MAX_CHARS` in the db.reels import):

```python
@router.post("/api/reels/{slug}/items/note")
def api_set_item_note(slug: str, body: ItemNoteBody, request: Request):
    conn = _conn(request)
    reel = _reel_or_404(conn, slug)
    if not set_item_note(conn, reel["id"], body.source_id,
                         body.start_ms, body.end_ms, body.note):
        raise HTTPException(status_code=404, detail="No such item in this reel")
    return {"ok": True}
```

`api_render_reel` signature: `def api_render_reel(slug: str, request: Request, body: RenderBody | None = None):` and the enqueue line becomes
`job_id, already_running = jobq.enqueue_reel_once(conn, reel["id"], numbered=body.numbered if body else False)`.

`splitstep/db/jobs.py` `enqueue_reel_once`: signature `(conn, reel_id: str, numbered: bool = False)`; the INSERT's payload becomes `json.dumps({"reel_id": reel_id, "numbered": numbered})`. Extend the docstring with one sentence: the in-flight check stays reel-id-only on purpose — plain and numbered renders write the same output file, so two kinds racing is the same hazard as two of one kind.

- [ ] **Step 4: Run** `tests/test_api_reels.py tests/test_jobs.py -q` → PASS. Ruff clean.

- [ ] **Step 5: Commit** — `feat(api): item notes and a numbered flag on render`

---

### Task 3: The numbered render itself

**Files:**
- Create: `splitstep/media/numbered.py`, its test `tests/test_numbered.py`
- Modify: `splitstep/resources.py` (font resolver), `splitstep/jobs/handlers.py` (`handle_reel` numbered branch)
- Test: `tests/test_handler_reel.py` (numbered end-to-end on tiny fixtures)

**Interfaces:**
- Consumes: `payload["numbered"]` (Task 2), `ReelItem.note` (Task 1), `get_color_profile` (`splitstep/db/settings.py`), `HLG_PROFILE`/`CLIP_FPS`/`CLIP_CRF`/`run_ffmpeg` (`splitstep/media/transcode.py`), `concat_clips`.
- Produces: `resources.drawtext_font() -> str`; `make_numbered_intermediate(src, dst, *, counter: str, note: str, color_profile, on_progress=None)`; `drawtext_filters(counter, note, font) -> str` (pure, testable).

- [ ] **Step 1: Font resolver.** In `splitstep/resources.py`:

```python
# macOS system faces drawtext can use directly, most specific first. .ttf
# only -- .ttc collections are inconsistently handled by fontfile=. The
# bundled font (Phase 3 ships an OFL face) wins when present; the env var
# is the escape hatch for a Mac without these paths.
_SYSTEM_FONTS = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)


def drawtext_font() -> str:
    bundled = _bundled("font.ttf")
    if bundled is not None:
        return str(bundled)
    env = os.environ.get("SPLITSTEP_FONT")
    if env and Path(env).is_file():
        return env
    for candidate in _SYSTEM_FONTS:
        if Path(candidate).is_file():
            return candidate
    raise RuntimeError(
        "No font for the numbered overlay. Set SPLITSTEP_FONT to a .ttf path."
    )
```

(Add `import os` to resources.py.) Tests in `tests/test_resources.py`: bundled wins; env fallback with a tmp .ttf; on this Mac the system path resolves.

- [ ] **Step 2: Failing tests for the filter builder** in `tests/test_numbered.py`:

```python
from splitstep.media.numbered import drawtext_filters


def test_counter_and_note_are_two_drawtext_filters():
    vf = drawtext_filters("3/20", "match point", "/tmp/f.ttf")
    assert vf.count("drawtext=") == 2
    assert "text='3/20'" in vf and "text='match point'" in vf
    assert vf.count("expansion=none") == 2


def test_no_note_means_one_filter():
    assert drawtext_filters("3/20", "", "/tmp/f.ttf").count("drawtext=") == 1


def test_note_text_is_escaped_for_the_filtergraph():
    vf = drawtext_filters("1/2", "it's 50%: a,b\\c", "/tmp/f.ttf")
    # The five characters that terminate or re-interpret a drawtext value.
    assert "\\'" in vf and "\\:" in vf and "\\," in vf and "\\\\" in vf
```

- [ ] **Step 3: Implement `splitstep/media/numbered.py`:**

```python
"""Burned-in counter and note for a numbered reel render.

Each reel item gets one drawtext re-encode into a temp intermediate at the
library's locked colour profile; the existing concat pipeline (pre-flight
parameter check, -c copy, duration probe) then runs over the intermediates
unchanged -- every intermediate is encoded identically, so stream-copy
concat of them stays valid and both guards keep doing their jobs. Shared
clip files are never modified: the same clip can be #3 in one reel and #11
in another.
"""

from pathlib import Path

from splitstep.media.transcode import CLIP_CRF, CLIP_FPS, ProgressFn, run_ffmpeg

# Sized against the locked 3840x2160 frame: legible on a phone screen
# without shouting over the footage.
_MARGIN = 64
_COUNTER_SIZE = 120
_NOTE_SIZE = 72
_BOX = "box=1:boxcolor=black@0.45:boxborderw=24"


def _escape(text: str) -> str:
    """Escape a literal for a drawtext option value.

    expansion=none already keeps %-sequences literal; what remains is the
    filtergraph parser itself: backslash first (it is the escape), then the
    quote that would end the value, then the option and filter separators.
    """
    for ch in ("\\", "'", ":", ","):
        text = text.replace(ch, "\\" + ch)
    return text


def drawtext_filters(counter: str, note: str, font: str) -> str:
    common = f"fontfile='{_escape(font)}':fontcolor=white:{_BOX}:expansion=none"
    filters = [
        f"drawtext=text='{_escape(counter)}':{common}"
        f":fontsize={_COUNTER_SIZE}:x={_MARGIN}:y={_MARGIN}"
    ]
    if note:
        filters.append(
            f"drawtext=text='{_escape(note)}':{common}"
            f":fontsize={_NOTE_SIZE}:x={_MARGIN}:y={_MARGIN + _COUNTER_SIZE + 48}"
        )
    return ",".join(filters)


def make_numbered_intermediate(
    src: Path,
    dst: Path,
    *,
    counter: str,
    note: str,
    color_profile: tuple[str, str, str, str],
    font: str,
    on_progress: ProgressFn | None = None,
    duration_ms: int | None = None,
) -> None:
    """One clip, re-encoded whole with the overlay, at the locked profile.

    Video settings mirror make_clip's encode exactly -- same encoder, rate,
    CRF, pixel format, colour flags -- so every intermediate carries
    identical codec parameters and concat's pre-flight sees no divergence.
    Audio is stream-copied: the AAC track is already at the profile
    (make_clip wrote it), re-encoding it would only generation-loss the one
    part of the clip the overlay does not touch.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-i", str(src),
        "-vf", drawtext_filters(counter, note, font),
        "-r", str(CLIP_FPS),
        "-c:v", "libx264",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-color_range", color_profile[0],
        "-colorspace", color_profile[1],
        "-color_primaries", color_profile[3],
        "-color_trc", color_profile[2],
        "-crf", str(CLIP_CRF),
        "-preset", "medium",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(dst),
    ], on_progress=on_progress, total_ms=duration_ms)
```

(Check `run_ffmpeg`'s actual signature in transcode.py — it takes `on_progress`/`total_ms` per the make_clip call; match it. The encode-flag indices follow the canonical `(range, space, trc, primaries)` order — same mapping Task 10 of Phase 1 used.)

- [ ] **Step 4: `handle_reel` numbered branch.** After the `missing` check and BEFORE the `require_free` line, branch on `payload.get("numbered")`. Numbered path (imports: `shutil`, `resources`, `get_color_profile`, `HLG_PROFILE`, the new module):

```python
    if payload.get("numbered"):
        # Space: every input re-encoded (~input size again) plus the concat
        # of the copies -- triple the plain render's bound, and the same
        # refuse-early rationale.
        library.require_free(sum(p.stat().st_size for p in inputs) * 3)
        # Clips exist (the missing check above), so the profile was locked
        # when they were exported -- or they predate migration 011, which is
        # exactly what HLG_PROFILE is the legacy answer for.
        profile = get_color_profile(conn) or HLG_PROFILE
        font = resources.drawtext_font()
        tmp_dir = library.reels_dir / f".{reel['slug']}.numbered.{uuid.uuid4().hex[:8]}"
        tmp_dir.mkdir(parents=True)
        try:
            intermediates: list[Path] = []
            total = len(items)
            for i, (item, src) in enumerate(zip(items, inputs), start=1):
                dst_i = tmp_dir / f"{i:03d}.mp4"
                make_numbered_intermediate(
                    src, dst_i,
                    counter=f"{i}/{total}",
                    note=item.note,
                    color_profile=profile,
                    font=font,
                )
                intermediates.append(dst_i)
                # One coarse tick per finished clip: the per-clip encode is
                # the unit a human waits through, and threading ffmpeg's own
                # progress through N sequential encodes would need offset
                # bookkeeping this job does not otherwise carry.
                progress(i / (total + 1))
            mode = concat_clips(intermediates, dst, on_progress=progress)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    else:
        ...  # existing require_free + concat_clips path, unchanged
```

Keep the existing `mode == "reencode"` logging and `mark_rendered` AFTER the branch, shared by both paths. `shutil` is already imported in handlers.py (check; add if not). The dot-prefixed temp dir keeps `rendered_file`'s containment and the reels listing indifferent to it, and `ignore_errors=True` on cleanup must not mask the encode's own exception — the `finally` placement does that.

- [ ] **Step 5: End-to-end test** in `tests/test_handler_reel.py` (reuse its existing fixture machinery — it already builds reels over real tiny clips): a two-item reel with one note, `handle_reel(library, {"reel_id": rid, "numbered": True})` → rendered file exists, its probed duration ≈ sum of inputs (same tolerance the plain-render test uses), no `.numbered.` temp dir left in `reels/`, and the plain render test still passes untouched. Also: numbered render of a reel whose items have empty notes succeeds (counter only).

- [ ] **Step 6: Run** `tests/test_numbered.py tests/test_resources.py tests/test_handler_reel.py -q` → PASS. Then the FULL suite once → green. Ruff clean.

- [ ] **Step 7: Commit** — `feat(reels): burn a counter and note into a numbered render`

---

### Task 4: Web — note entry, numbered toggle, preview badge

**Files:**
- Modify: `web/src/lib/types.ts` (ReelItem gains `note: string`), `web/src/lib/api.ts`, `web/src/lib/reels.ts` (+ its test file), `web/src/components/ReelItemList.svelte`, `web/src/components/ReelPreview.svelte`, `web/src/routes/Reel.svelte`
- Test: `web/tests/reels.test.ts` (or wherever `lib/reels.ts` is covered — find it)

**Interfaces:**
- Consumes: `POST /api/reels/{slug}/items/note`, render `{numbered}` (Task 2), `items[].note` in the detail payload (Task 1).

- [ ] **Step 1: Setup** — `cd web && npm install` (worktree has no node_modules; without this, npx falls back to the main checkout and dies on vite.config).

- [ ] **Step 2: Lib first (TDD).** In `web/src/lib/reels.ts` add and test:

```ts
export const ITEM_NOTE_MAX_CHARS = 40

/** Trim, cap-check. null = refuse (over-long); '' = clear the note. */
export function normalizedItemNote(raw: string): string | null {
  const note = raw.trim()
  return note.length > ITEM_NOTE_MAX_CHARS ? null : note
}
```

Tests: trims, passes an exactly-40 note, nulls a 41-char note, empty string stays `''` (clearing is legal — the server stores `''`).

- [ ] **Step 3: api.ts** — mirror the file's existing shapes:

```ts
  setReelItemNote: (slug: string, span: SpanRef, note: string) =>
    req<{ ok: boolean }>(`/api/reels/${slug}/items/note`, {
      method: 'POST', body: JSON.stringify({ ...span, note }),
    }),
```

and `renderReel: (slug: string, numbered: boolean) => req<RenderResult>(…, { method: 'POST', body: JSON.stringify({ numbered }) })` — update its one caller.

- [ ] **Step 4: Components** (thin shells; follow the file's existing idioms exactly):
  - `ReelItemList.svelte`: each row shows the note in `font-data text-data text-dim` when present, and an inline edit affordance following the EXISTING rename pattern in `Reel.svelte` — Enter commits, Escape cancels, **no `onblur` commit** (saveName's comment explains the failure mode; same rule here). Commit goes through a new `onnote(item, note)` callback prop, wired in `Reel.svelte` through the same `mutate()` funnel as every other action (`api.setReelItemNote`, failure text "Couldn't save that note"). An over-long draft disables Save via `normalizedItemNote(...) === null` exactly as rename uses `normalizedReelName`.
  - `Reel.svelte`: a `numbered` checkbox `$state` beside the Render button, `font-data text-data`, label "numbered"; `render()` passes it to `api.renderReel(slug, numbered)`. Title/help: numbered re-encodes every clip (~4-8x footage duration) — plain render stays the fast default.
  - `ReelPreview.svelte`: it already tracks the current item index while seeking spans in order; overlay a `font-data` badge on the video corner — `{i + 1}/{items.length}` plus the item's note beneath when present. Letterbox stays literal `bg-black`; badge uses tokens (`text-fg` on `bg-black/60` is acceptable as the video-overlay exception — match how existing overlays in VideoDeck do it if one exists; otherwise `bg-black/60 text-white` is the letterbox-literal exception, commented).

- [ ] **Step 5: Verify** — `npx vitest run` all green; `npm run check` clean; `npm run build` succeeds.

- [ ] **Step 6: Python suite unaffected** — no server files touched in this task; skip the Python suite.

- [ ] **Step 7: Commit** — `feat(web): notes on reel items, a numbered render toggle, a preview badge`

---

### Task 5: Final verification + docs

- [ ] Full Python suite (foreground, once) + ruff → green; `npx vitest run` + `npm run check` → green.
- [ ] `CLAUDE.md`: one sentence in the Reels section — reels can render numbered (counter + per-item note burned in via per-clip intermediates at the locked profile; plain render unchanged).
- [ ] Spec status: mark Phase 4 shipped-on-branch in `docs/superpowers/specs/2026-08-26-mac-app-distribution-design.md`'s Phase 4 section (one line, don't rewrite it).
- [ ] Commit — `docs: numbered reels are real`.
