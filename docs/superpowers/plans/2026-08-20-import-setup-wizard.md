# Import Setup Wizard and Source Rotation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move orientation and play-region confirmation ahead of every expensive step, so a dropped file is registered in seconds, a human sets rotation and court quad against real preview frames, and only then does the library transcode a proxy and run detection.

**Architecture:** `ingest` splits into register-only ingest plus a new `build_proxy` job. Rotation becomes a stored `sources.rotation_deg` column applied deterministically by `make_proxy` (`-noautorotate` plus an explicit `transpose` chain). A new `preview.jpg` route extracts frames from the original file so the wizard has something to show before any proxy exists. The wizard is a new SPA route reusing the quad drag surface extracted out of `QuadEditor`.

**Tech Stack:** Python 3.12, FastAPI, sqlite3, ffmpeg/ffprobe (subprocess), pytest, ruff · Vite, Svelte 5 runes, TypeScript, Tailwind v4, Vitest.

**Spec:** `docs/superpowers/specs/2026-08-20-import-setup-wizard-design.md`
**Depends on:** Plan 1 (`2026-08-19-backend-core.md`) and Plan 2 (`2026-08-19-review-ui.md`), both complete and merged to `master`.

## Global Constraints

- Python **3.12** exactly, env `bootleg`. ffmpeg on `PATH` (9.0.1 on the dev machine).
- Explicit SQL only, **no ORM**. Migrations are numbered `.sql` files under `bootleg/db/migrations/`, applied by `PRAGMA user_version`.
- **Svelte 5 runes only** (`$state`/`$derived`/`$effect`/`$props`). No `export let`, no `$:`, no stores.
- **No YOLO inference inside tests.** Real ffmpeg in tests is fine and already used — `tests/test_probe.py` synthesizes clips with `lavfi`.
- Proxy video stays **H.264 with `-g 30`**, 1080p, `yuv420p`. Rotation changes the filter chain only; it must never change the codec, GOP, or pixel format.
- `det_start_ms` / `det_end_ms` are written once at rally creation and never updated.
- Every ffmpeg subprocess call captures stderr and raises on non-zero exit. Request-path calls pass a timeout; background-job calls do not.
- Legal rotations are exactly `0, 90, 180, 270`. Any other value is a `ValueError` in the library and a 400 at the API boundary.
- `rotation_deg` means **degrees clockwise applied to the coded frame** to get the upright image.
- Run `ruff check .` and `npx svelte-check --threshold warning` before every commit; both must be clean.

---

## File Structure

```
bootleg/
  db/migrations/002_rotation.sql        NEW  rotation_deg column
  db/sessions.py                        MOD  add_source(rotation_deg=), set_source_rotation()
  media/probe.py                        MOD  MediaInfo.rotation_deg, display_size()
  media/transcode.py                    MOD  rotation_filter(), make_proxy(rotation_deg=)
  media/frames.py                       MOD  extract_frame(rotation_deg=, hwaccel=)
  jobs/handlers.py                      MOD  ingest register-only, handle_build_proxy
  setup.py                              NEW  queue_setup() shared by API and CLI
  api/routes.py                         MOD  GET /api/sources/{id}, POST .../setup, preview.jpg
  cli.py                                MOD  bootleg setup, doctor source table
tests/
  test_probe.py                         MOD  rotation parsing, display_size
  test_transcode.py                     MOD  rotation_filter, autorotate equivalence
  test_handlers.py                      MOD  register-only ingest, build_proxy
  test_api.py                           MOD  setup route, preview route
  test_cli.py                           MOD  bootleg setup
  test_db.py                            MOD  migration 002
web/src/
  lib/preview.ts                        NEW  previewTimestamps()
  lib/router.svelte.ts                  MOD  #/setup/<id>
  lib/api.ts                            MOD  getSource, setup, previewUrl
  lib/types.ts                          MOD  Source.rotation_deg
  components/QuadCanvas.svelte          NEW  image + handles + scrub, extracted
  components/QuadEditor.svelte          MOD  consumes QuadCanvas
  routes/Setup.svelte                   NEW  the wizard
  routes/Library.svelte                 MOD  "Set up" cards
  App.svelte                            MOD  setup route
web/tests/
  preview.test.ts                       NEW
  setup-wizard.test.ts                  NEW
  router.test.ts                        MOD
```

---

### Task 1: Commit the frame scrubber already in the working tree

The session-page quad editor already gained a frame scrubber (slider, frame/second stepping, timecode, `lastSafeFrameMs`). It is written and green but uncommitted; the wizard consumes it in Task 14, so land it first.

**Files:**
- Modify: `web/src/lib/time.ts`, `web/src/components/QuadEditor.svelte`
- Test: `web/tests/time.test.ts`, `web/tests/quad-editor-scrub.test.ts`

- [ ] **Step 1: Confirm the suite is green**

Run: `cd web && npx vitest run && npx svelte-check --threshold warning`
Expected: `182 passed`, `0 ERRORS 0 WARNINGS`

- [ ] **Step 2: Commit**

```bash
git add web/src/lib/time.ts web/src/components/QuadEditor.svelte \
        web/tests/time.test.ts web/tests/quad-editor-scrub.test.ts
git commit -m "feat(web): scrub the play-region frame instead of pinning it to t=0"
```

---

### Task 2: probe reports display rotation and display dimensions

**Files:**
- Modify: `bootleg/media/probe.py`
- Test: `tests/test_probe.py`

**Interfaces:**
- Produces: `MediaInfo.rotation_deg: int` (0/90/180/270, clockwise); `display_size(width: int, height: int, rotation_deg: int) -> tuple[int, int]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_probe.py -- append

import subprocess
import pytest
from bootleg.media.probe import display_size, probe


@pytest.fixture
def rotated_video(tmp_path, sample_video):
    """The 320x240 sample re-muxed with a 90 degree display matrix."""
    out = tmp_path / "rotated.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-display_rotation", "90", "-i", str(sample_video),
         "-c", "copy", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_probe_reports_zero_rotation_for_an_untagged_clip(sample_video):
    assert probe(sample_video).rotation_deg == 0


def test_probe_reads_a_display_matrix_rotation(rotated_video):
    assert probe(rotated_video).rotation_deg in (90, 270)


def test_probe_still_reports_coded_dimensions_for_a_rotated_clip(rotated_video):
    info = probe(rotated_video)
    assert (info.width, info.height) == (320, 240)


def test_display_size_swaps_the_axes_on_a_quarter_turn():
    assert display_size(3840, 2160, 90) == (2160, 3840)
    assert display_size(3840, 2160, 270) == (2160, 3840)


def test_display_size_is_unchanged_on_a_half_turn():
    assert display_size(3840, 2160, 0) == (3840, 2160)
    assert display_size(3840, 2160, 180) == (3840, 2160)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_probe.py -k "rotation or display_size" -v`
Expected: FAIL — `ImportError: cannot import name 'display_size'`

- [ ] **Step 3: Implement**

```python
# bootleg/media/probe.py

@dataclass(frozen=True)
class MediaInfo:
    duration_ms: int
    width: int          # coded width, before any display matrix is applied
    height: int         # coded height, likewise
    fps: float
    recorded_at: str | None
    has_audio: bool
    codec_name: str
    rotation_deg: int   # clockwise degrees to apply to the coded frame


def _display_rotation(video: dict) -> int:
    """Clockwise degrees a player would rotate this stream by to display it.

    ffprobe reports the Display Matrix angle counter-clockwise, so the sign
    flips here. Anything that is not a quarter turn (a matrix carrying a
    flip, or a stream with no matrix at all) reads as 0: BootlegVision only
    ever encodes right angles, and a bogus value must not reach
    `rotation_filter`, which raises on one.
    """
    for side in video.get("side_data_list", []):
        raw = side.get("rotation")
        if raw is None:
            continue
        try:
            deg = int(round(float(raw)))
        except (TypeError, ValueError):
            continue
        deg = (-deg) % 360
        return deg if deg in (0, 90, 180, 270) else 0
    return 0


def display_size(width: int, height: int, rotation_deg: int) -> tuple[int, int]:
    """Dimensions after `rotation_deg` is applied to a coded `width x height`."""
    return (height, width) if rotation_deg in (90, 270) else (width, height)
```

Add `rotation_deg=_display_rotation(video)` to the `MediaInfo(...)` construction at the end of `probe()`.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_probe.py -v`
Expected: PASS (all, including the pre-existing ones)

- [ ] **Step 5: Commit**

```bash
git add bootleg/media/probe.py tests/test_probe.py
git commit -m "feat(media): read display-matrix rotation in probe"
```

---

### Task 3: rotation_filter and a rotation-aware make_proxy

The sign convention chosen in Task 2 is settled here, empirically: a clip tagged with a display matrix must come out of our explicit pipeline pixel-identical to what ffmpeg's own autorotate produces.

**Files:**
- Modify: `bootleg/media/transcode.py`
- Test: `tests/test_transcode.py`

**Interfaces:**
- Consumes: `MediaInfo.rotation_deg` from Task 2
- Produces: `rotation_filter(deg: int) -> str`; `make_proxy(src, dst, accel=None, rotation_deg: int = 0)`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_transcode.py -- append

import subprocess
import pytest
from bootleg.media.probe import probe
from bootleg.media.transcode import make_proxy, rotation_filter


def test_rotation_filter_maps_every_right_angle():
    assert rotation_filter(0) == ""
    assert rotation_filter(90) == "transpose=1"
    assert rotation_filter(180) == "transpose=1,transpose=1"
    assert rotation_filter(270) == "transpose=2"


def test_rotation_filter_rejects_anything_else():
    with pytest.raises(ValueError, match="0, 90, 180 or 270"):
        rotation_filter(45)


def test_make_proxy_at_zero_rotation_ignores_the_display_matrix(tmp_path, sample_video):
    """A tagged clip transcoded at rotation 0 keeps its coded orientation.

    This is the whole point of -noautorotate: the stored rotation decides,
    not ffmpeg's default.
    """
    tagged = tmp_path / "tagged.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-display_rotation", "90", "-i", str(sample_video),
         "-c", "copy", str(tagged)],
        check=True, capture_output=True,
    )
    out = tmp_path / "proxy.mp4"
    make_proxy(tagged, out, rotation_deg=0)
    info = probe(out)
    assert info.width > info.height


def test_make_proxy_at_the_probed_rotation_matches_ffmpeg_autorotate(tmp_path, sample_video):
    """Settles the sign convention: probe + rotation_filter must agree with
    what every other player would show."""
    tagged = tmp_path / "tagged.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-display_rotation", "90", "-i", str(sample_video),
         "-c", "copy", str(tagged)],
        check=True, capture_output=True,
    )
    ours = tmp_path / "ours.png"
    theirs = tmp_path / "theirs.png"
    deg = probe(tagged).rotation_deg
    vf = ",".join(f for f in (rotation_filter(deg), "scale=-2:120") if f)
    subprocess.run(
        ["ffmpeg", "-y", "-noautorotate", "-i", str(tagged), "-vf", vf,
         "-frames:v", "1", str(ours)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(tagged), "-vf", "scale=-2:120",
         "-frames:v", "1", str(theirs)],
        check=True, capture_output=True,
    )
    assert ours.read_bytes() == theirs.read_bytes()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_transcode.py -k "rotation" -v`
Expected: FAIL — `ImportError: cannot import name 'rotation_filter'`

- [ ] **Step 3: Implement**

```python
# bootleg/media/transcode.py

# transpose=1 is 90 degrees clockwise, transpose=2 is 90 counter-clockwise.
# 180 is two clockwise quarter turns rather than hflip,vflip: identical
# result, one filter name to reason about instead of two.
_TRANSPOSE = {
    0: "",
    90: "transpose=1",
    180: "transpose=1,transpose=1",
    270: "transpose=2",
}


def rotation_filter(deg: int) -> str:
    """ffmpeg filter chain rotating a coded frame `deg` degrees clockwise."""
    try:
        return _TRANSPOSE[deg]
    except KeyError:
        raise ValueError(f"rotation must be 0, 90, 180 or 270, got {deg!r}") from None


def make_proxy(src: Path, dst: Path, accel: Accel | None = None, rotation_deg: int = 0) -> None:
    """1080p H.264 with a 1-second GOP. H.264 because browser HEVC is a coin flip.

    Orientation comes from `rotation_deg`, never from the source's display
    matrix: `-noautorotate` disables ffmpeg's default so a rotation this
    library did not choose can never reach the scale filter. It reached it
    once -- a 3840x2160 clip tagged rotation=90 scaled to 608x1080, losing
    two thirds of the scene's pixels and with them every person detection.
    """
    accel = accel or detect_accel()
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = ",".join(f for f in (rotation_filter(rotation_deg), "scale=-2:1080:flags=bicubic") if f)

    args: list[str] = ["-noautorotate"]
    if accel.hwaccel:
        args += ["-hwaccel", accel.hwaccel]
    args += [
        "-i", str(src),
        "-vf", vf,
        ...  # rest unchanged
    ]
```

Keep every other flag exactly as it is today.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_transcode.py -v`
Expected: PASS. If `test_make_proxy_at_the_probed_rotation_matches_ffmpeg_autorotate` fails, the sign in `_display_rotation` is inverted — change `(-deg) % 360` to `deg % 360` and re-run. Do not "fix" it by editing `_TRANSPOSE`.

- [ ] **Step 5: Commit**

```bash
git add bootleg/media/transcode.py tests/test_transcode.py
git commit -m "feat(media): make proxy rotation explicit, never autorotate"
```

---

### Task 4: rotation_deg column and source write helpers

**Files:**
- Create: `bootleg/db/migrations/002_rotation.sql`
- Modify: `bootleg/db/sessions.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `add_source(..., rotation_deg: int = 0)`; `set_source_rotation(conn, source_id: str, rotation_deg: int) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_db.py -- append

from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import (
    add_source, find_or_create_session_for_date, get_source, set_source_rotation,
)


def test_migration_adds_rotation_defaulting_to_zero(tmp_path):
    conn = connect(tmp_path / "l.db")
    migrate(conn)
    session_id = find_or_create_session_for_date(conn, "2026-08-20")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-20", duration_ms=1000,
        width=1920, height=1080, fps=30.0, original_name="a.mov",
    )
    assert get_source(conn, source_id)["rotation_deg"] == 0


def test_add_source_stores_an_explicit_rotation(tmp_path):
    conn = connect(tmp_path / "l.db")
    migrate(conn)
    session_id = find_or_create_session_for_date(conn, "2026-08-20")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-20", duration_ms=1000,
        width=2160, height=3840, fps=30.0, original_name="a.mov", rotation_deg=90,
    )
    assert get_source(conn, source_id)["rotation_deg"] == 90


def test_set_source_rotation_updates_in_place(tmp_path):
    conn = connect(tmp_path / "l.db")
    migrate(conn)
    session_id = find_or_create_session_for_date(conn, "2026-08-20")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-20", duration_ms=1000,
        width=1920, height=1080, fps=30.0, original_name="a.mov",
    )
    set_source_rotation(conn, source_id, 270)
    assert get_source(conn, source_id)["rotation_deg"] == 270


def test_set_source_rotation_rejects_a_non_right_angle(tmp_path):
    conn = connect(tmp_path / "l.db")
    migrate(conn)
    session_id = find_or_create_session_for_date(conn, "2026-08-20")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-20", duration_ms=1000,
        width=1920, height=1080, fps=30.0, original_name="a.mov",
    )
    with pytest.raises(ValueError, match="0, 90, 180 or 270"):
        set_source_rotation(conn, source_id, 45)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_db.py -k rotation -v`
Expected: FAIL — `no such column: rotation_deg`

- [ ] **Step 3: Write the migration**

```sql
-- bootleg/db/migrations/002_rotation.sql
--
-- Clockwise degrees applied to a source's coded frame when its proxy is
-- built. Existing rows default to 0 and are deliberately NOT backfilled
-- from their originals' display matrices: their proxies were encoded under
-- ffmpeg's autorotate, so an inferred value would describe an intent the
-- file on disk does not match. Re-run setup on such a source instead.
ALTER TABLE sources ADD COLUMN rotation_deg INTEGER NOT NULL DEFAULT 0;
```

- [ ] **Step 4: Implement the helpers**

```python
# bootleg/db/sessions.py

from bootleg.media.transcode import rotation_filter


def _check_rotation(rotation_deg: int) -> int:
    # rotation_filter is the single source of truth for what is legal; a
    # CHECK constraint would surface a bad value as an opaque IntegrityError
    # from three layers down instead of a message naming the four options.
    rotation_filter(rotation_deg)
    return rotation_deg


def set_source_rotation(conn: sqlite3.Connection, source_id: str, rotation_deg: int) -> None:
    conn.execute(
        "UPDATE sources SET rotation_deg=? WHERE id=?",
        (_check_rotation(rotation_deg), source_id),
    )
    conn.commit()
```

In `add_source`, add the keyword-only parameter `rotation_deg: int = 0`, call `_check_rotation(rotation_deg)`, and extend the INSERT column list and values tuple with it.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_db.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add bootleg/db/migrations/002_rotation.sql bootleg/db/sessions.py tests/test_db.py
git commit -m "feat(db): store a per-source rotation"
```

---

### Task 5: ingest becomes register-only

**Files:**
- Modify: `bootleg/jobs/handlers.py`
- Test: `tests/test_handlers.py`

**Interfaces:**
- Consumes: `probe().rotation_deg`, `display_size()` (Task 2), `add_source(rotation_deg=)` (Task 4)
- Produces: sources left at status `needs_setup`, no `detect` job enqueued

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_handlers.py -- append

import json
from bootleg.db.sessions import get_source
from bootleg.jobs.handlers import handle_ingest


def test_ingest_registers_without_transcoding(library, sample_video, monkeypatch):
    called = []
    monkeypatch.setattr(
        "bootleg.jobs.handlers.make_proxy",
        lambda *a, **k: called.append(a),
    )
    src = library.inbox / "IMG_0001.MOV"
    src.write_bytes(sample_video.read_bytes())

    handle_ingest(library, {"path": str(src)})

    conn = connect(library.db_path)
    row = conn.execute("SELECT * FROM sources").fetchone()
    assert row["status"] == "needs_setup"
    assert called == []


def test_ingest_moves_the_original_into_the_session_tree(library, sample_video):
    src = library.inbox / "IMG_0002.MOV"
    src.write_bytes(sample_video.read_bytes())

    handle_ingest(library, {"path": str(src)})

    conn = connect(library.db_path)
    row = conn.execute("SELECT * FROM sources").fetchone()
    dest = library.source_dir(row["session_id"], row["idx"])
    assert not src.exists()
    assert (dest / "original.mov").is_file()


def test_ingest_enqueues_nothing(library, sample_video):
    src = library.inbox / "IMG_0003.MOV"
    src.write_bytes(sample_video.read_bytes())

    handle_ingest(library, {"path": str(src)})

    conn = connect(library.db_path)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_ingest_retry_after_the_original_moved_is_a_no_op(library, sample_video):
    src = library.inbox / "IMG_0004.MOV"
    src.write_bytes(sample_video.read_bytes())
    handle_ingest(library, {"path": str(src)})

    # The worker crashed and reclaim_stale requeued the same payload; the
    # inbox path is gone because the first attempt already moved it.
    handle_ingest(library, {"path": str(src)})

    conn = connect(library.db_path)
    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1


def test_ingest_stores_rotation_and_display_dimensions(library, tmp_path, sample_video):
    tagged = tmp_path / "tagged.mov"
    subprocess.run(
        ["ffmpeg", "-y", "-display_rotation", "90", "-i", str(sample_video),
         "-c", "copy", str(tagged)],
        check=True, capture_output=True,
    )
    src = library.inbox / "IMG_0005.MOV"
    src.write_bytes(tagged.read_bytes())

    handle_ingest(library, {"path": str(src)})

    conn = connect(library.db_path)
    row = conn.execute("SELECT * FROM sources").fetchone()
    assert row["rotation_deg"] in (90, 270)
    # 320x240 coded, quarter-turned for display.
    assert (row["width"], row["height"]) == (240, 320)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_handlers.py -k ingest -v`
Expected: FAIL — status is `ingested`, and a `detect` job exists

- [ ] **Step 3: Rewrite handle_ingest**

```python
def handle_ingest(library: Library, payload: dict) -> None:
    """Register a dropped file. No transcode, no detection.

    Both wait for a human to confirm orientation and play region in the
    setup wizard, which enqueues `build_proxy`. Registering is seconds of
    probing and a move, so a file dropped in the inbox shows up in the UI
    immediately instead of after a ten-minute round trip that may have been
    encoding it sideways the whole time.
    """
    src = Path(payload["path"])
    conn = _open(library)
    session_id: str | None = None
    source_id: str | None = None

    try:
        if not src.exists():
            # A requeued job whose first attempt already moved the file.
            # Nothing to redo: the source row is committed and the original
            # is in place.
            return
        info = probe(src)
        # The transcode's space is checked in build_proxy, where it happens.
        # A move needs only what the file already occupies.
        library.require_free(src.stat().st_size)
        played_on = _played_on(info.recorded_at, src)
        session_id = find_or_create_session_for_date(conn, played_on)

        existing = get_source_by_original_name(conn, session_id, src.name)
        if existing is not None:
            source_id, idx = existing["id"], existing["idx"]
        else:
            width, height = display_size(info.width, info.height, info.rotation_deg)
            source_id, idx = add_source(
                conn, session_id,
                recorded_at=info.recorded_at or played_on,
                duration_ms=info.duration_ms,
                width=width, height=height, fps=info.fps,
                original_name=src.name,
                rotation_deg=info.rotation_deg,
            )

        dest_dir = library.source_dir(session_id, idx)
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), dest_dir / f"original{src.suffix.lower()}")

        set_source_status(conn, source_id, "needs_setup")
        set_session_status(conn, session_id, "needs_setup")
    except Exception as exc:
        if source_id is not None:
            set_source_status(conn, source_id, "failed")
        if session_id is not None:
            set_session_status(conn, session_id, "failed")
        _move_to_failed(library, src, f"{type(exc).__name__}: {exc}")
        raise
```

Import `display_size` from `bootleg.media.probe`.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_handlers.py -v`
Expected: PASS. Existing ingest tests asserting `status == "ingested"` or a queued `detect` job must be updated to the new contract, not deleted.

- [ ] **Step 5: Commit**

```bash
git add bootleg/jobs/handlers.py tests/test_handlers.py
git commit -m "feat(jobs): ingest registers a source without transcoding it"
```

---

### Task 6: the build_proxy job

**Files:**
- Modify: `bootleg/jobs/handlers.py`
- Test: `tests/test_handlers.py`

**Interfaces:**
- Consumes: `make_proxy(..., rotation_deg=)` (Task 3)
- Produces: `handle_build_proxy(library, payload: {"source_id": str})`; `HANDLERS["build_proxy"]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_handlers.py -- append

from bootleg.jobs.handlers import handle_build_proxy


def _registered(library, sample_video, name="IMG_1000.MOV"):
    src = library.inbox / name
    src.write_bytes(sample_video.read_bytes())
    handle_ingest(library, {"path": str(src)})
    conn = connect(library.db_path)
    return conn, conn.execute("SELECT * FROM sources").fetchone()


def test_build_proxy_passes_the_stored_rotation(library, sample_video, monkeypatch):
    conn, row = _registered(library, sample_video)
    set_source_rotation(conn, row["id"], 270)
    seen = {}
    monkeypatch.setattr(
        "bootleg.jobs.handlers.make_proxy",
        lambda src, dst, rotation_deg=0: seen.update(rotation_deg=rotation_deg) or dst.touch(),
    )
    monkeypatch.setattr("bootleg.jobs.handlers.make_thumbs", lambda *a, **k: None)

    handle_build_proxy(library, {"source_id": row["id"]})

    assert seen["rotation_deg"] == 270


def test_build_proxy_enqueues_detect(library, sample_video, monkeypatch):
    conn, row = _registered(library, sample_video, name="IMG_1001.MOV")
    monkeypatch.setattr(
        "bootleg.jobs.handlers.make_proxy", lambda src, dst, rotation_deg=0: dst.touch()
    )
    monkeypatch.setattr("bootleg.jobs.handlers.make_thumbs", lambda *a, **k: None)

    handle_build_proxy(library, {"source_id": row["id"]})

    job = conn.execute("SELECT * FROM jobs WHERE type='detect'").fetchone()
    assert json.loads(job["payload"])["source_id"] == row["id"]
    assert conn.execute(
        "SELECT status FROM sources WHERE id=?", (row["id"],)
    ).fetchone()["status"] == "ingested"


def test_build_proxy_on_a_missing_source_raises(library):
    with pytest.raises(ValueError, match="No such source"):
        handle_build_proxy(library, {"source_id": "nope"})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_handlers.py -k build_proxy -v`
Expected: FAIL — `cannot import name 'handle_build_proxy'`

- [ ] **Step 3: Implement**

```python
def handle_build_proxy(library: Library, payload: dict) -> None:
    """Transcode a registered source's proxy at its chosen rotation.

    Idempotent by overwrite: a proxy half-written by a killed worker is
    worthless, so a retry re-encodes rather than trying to resume.
    """
    conn = _open(library)
    source = get_source(conn, payload["source_id"])
    if source is None:
        raise ValueError(f"No such source: {payload['source_id']}")

    src_dir = library.source_dir(source["session_id"], source["idx"])
    original = _original_path(src_dir)
    if original is None:
        raise ValueError(f"No original on disk for source {source['id']}")

    try:
        set_source_status(conn, source["id"], "building")
        # Proxy plus sprite sheet run roughly 1.5x the source in the worst case.
        library.require_free(int(original.stat().st_size * 1.5))
        make_proxy(original, src_dir / "proxy.mp4", rotation_deg=source["rotation_deg"])
        make_thumbs(src_dir / "proxy.mp4", src_dir / "thumbs.jpg")
    except Exception:
        set_source_status(conn, source["id"], "failed")
        set_session_status(conn, source["session_id"], "failed")
        raise

    set_source_status(conn, source["id"], "ingested")
    set_session_status(conn, source["session_id"], "detecting")
    jobq.enqueue(conn, "detect", {"source_id": source["id"]})


def _original_path(src_dir: Path) -> Path | None:
    matches = sorted(src_dir.glob("original.*"))
    return matches[0] if matches else None
```

Refactor `_audio_source` to call `_original_path` rather than repeating the glob, and register the handler:

```python
HANDLERS: dict[str, Handler] = {
    "ingest": handle_ingest,
    "build_proxy": handle_build_proxy,
    "detect": handle_detect,
}
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_handlers.py -v && ruff check .`
Expected: PASS, ruff clean

- [ ] **Step 5: Commit**

```bash
git add bootleg/jobs/handlers.py tests/test_handlers.py
git commit -m "feat(jobs): add build_proxy, the transcode ingest used to do"
```

---

### Task 7: queue_setup, shared by API and CLI

**Files:**
- Create: `bootleg/setup.py`
- Test: `tests/test_setup.py`

**Interfaces:**
- Consumes: `set_source_rotation` (Task 4), `handle_build_proxy` (Task 6)
- Produces: `queue_setup(conn, source_id: str, rotation_deg: int, preset_id: str) -> str` returning the job id; raises `LookupError` for an unknown source or preset, `ValueError` for a bad rotation, `RuntimeError` for a source mid-job

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_setup.py -- new file

import pytest
from bootleg.db.presets import create_preset
from bootleg.db.schema import connect, migrate
from bootleg.detect.geometry import Quad
from bootleg.db.sessions import (
    add_source, find_or_create_session_for_date, get_source, set_source_status,
)
from bootleg.setup import queue_setup


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "l.db")
    migrate(c)
    return c


def _source(conn, status="needs_setup"):
    session_id = find_or_create_session_for_date(conn, "2026-08-20")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-08-20", duration_ms=1000,
        width=1920, height=1080, fps=30.0, original_name="a.mov",
    )
    set_source_status(conn, source_id, status)
    return source_id


def _preset(conn):
    # create_preset takes a Quad, not raw points -- see bootleg/db/presets.py.
    return create_preset(conn, "court", Quad(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))))


def test_queue_setup_stores_both_and_enqueues_build_proxy(conn):
    source_id, preset_id = _source(conn), _preset(conn)

    job_id = queue_setup(conn, source_id, 90, preset_id)

    row = get_source(conn, source_id)
    assert row["rotation_deg"] == 90
    assert row["court_preset_id"] == preset_id
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert job["type"] == "build_proxy"


def test_queue_setup_rejects_a_bad_rotation(conn):
    with pytest.raises(ValueError, match="0, 90, 180 or 270"):
        queue_setup(conn, _source(conn), 45, _preset(conn))


def test_queue_setup_rejects_an_unknown_preset(conn):
    with pytest.raises(LookupError, match="preset"):
        queue_setup(conn, _source(conn), 0, "nope")


def test_queue_setup_rejects_an_unknown_source(conn):
    with pytest.raises(LookupError, match="source"):
        queue_setup(conn, "nope", 0, _preset(conn))


def test_queue_setup_allows_re_running_on_a_ready_source(conn):
    source_id = _source(conn, status="ready")
    assert queue_setup(conn, source_id, 0, _preset(conn))


def test_queue_setup_refuses_a_source_mid_job(conn):
    source_id = _source(conn, status="detecting")
    with pytest.raises(RuntimeError, match="detecting"):
        queue_setup(conn, source_id, 0, _preset(conn))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_setup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bootleg.setup'`

- [ ] **Step 3: Implement**

```python
# bootleg/setup.py
"""Applying a setup decision: rotation plus play region, then a rebuild.

One function, called by both the API route and the CLI, so the two cannot
drift on validation -- an invalid rotation must be rejected identically
whether it arrives over HTTP or from a terminal.
"""

import sqlite3

from bootleg.db import jobs as jobq
from bootleg.db.presets import get_preset
from bootleg.db.sessions import get_source, set_source_preset, set_source_rotation

# A source whose proxy is being written, or whose features are being
# extracted, cannot have either input changed underneath the running job.
BUSY_STATUSES = {"ingesting", "building", "detecting"}


def queue_setup(
    conn: sqlite3.Connection, source_id: str, rotation_deg: int, preset_id: str
) -> str:
    source = get_source(conn, source_id)
    if source is None:
        raise LookupError(f"No such source: {source_id}")
    if source["status"] in BUSY_STATUSES:
        raise RuntimeError(f"source is {source['status']}; wait for that job to finish")
    if get_preset(conn, preset_id) is None:
        raise LookupError(f"No such preset: {preset_id}")

    set_source_rotation(conn, source_id, rotation_deg)  # raises ValueError on a bad angle
    set_source_preset(conn, source_id, preset_id)
    return jobq.enqueue(conn, "build_proxy", {"source_id": source_id})
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_setup.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bootleg/setup.py tests/test_setup.py
git commit -m "feat: add queue_setup, the one path that applies a setup decision"
```

---

### Task 8: preview frames from the original

**Files:**
- Modify: `bootleg/media/frames.py`, `bootleg/api/routes.py`
- Test: `tests/test_frames.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: `rotation_filter` (Task 3), `_original_path` (Task 6)
- Produces: `extract_frame(src, dst, at_ms=0, width=1280, rotation_deg=0, hwaccel=None, timeout=...)`; route `GET /media/{session_id}/{idx}/preview.jpg?at_ms=&rot=`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_frames.py -- append

from bootleg.media.frames import extract_frame
from bootleg.media.probe import probe


def test_extract_frame_applies_a_rotation(tmp_path, sample_video):
    out = tmp_path / "rot.jpg"
    extract_frame(sample_video, out, at_ms=500, width=160, rotation_deg=90)
    info = probe(out)
    # 320x240 coded, scaled to 160 wide before rotation would give 160x120;
    # rotating first and then scaling to width 160 gives a tall frame.
    assert info.height > info.width


def test_extract_frame_rejects_a_bad_rotation(tmp_path, sample_video):
    with pytest.raises(ValueError, match="0, 90, 180 or 270"):
        extract_frame(sample_video, tmp_path / "x.jpg", rotation_deg=45)
```

```python
# tests/test_api.py -- append

def test_preview_serves_a_frame_from_the_original(client, registered_source):
    r = client.get(f"/media/{registered_source.session_id}/1/preview.jpg?at_ms=500&rot=0")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"


def test_preview_caches_per_rotation(client, registered_source, tmp_path):
    base = f"/media/{registered_source.session_id}/1/preview.jpg?at_ms=500"
    client.get(f"{base}&rot=0")
    client.get(f"{base}&rot=90")
    names = {p.name for p in registered_source.dir.glob("preview-*.jpg")}
    assert names == {"preview-0-500.jpg", "preview-90-500.jpg"}


def test_preview_rejects_a_non_right_angle(client, registered_source):
    r = client.get(f"/media/{registered_source.session_id}/1/preview.jpg?at_ms=0&rot=45")
    assert r.status_code == 400


def test_preview_clamps_past_the_end_of_the_clip(client, registered_source):
    r = client.get(f"/media/{registered_source.session_id}/1/preview.jpg?at_ms=99999999&rot=0")
    assert r.status_code == 200


def test_preview_404s_when_the_original_is_gone(client, registered_source):
    for p in registered_source.dir.glob("original.*"):
        p.unlink()
    r = client.get(f"/media/{registered_source.session_id}/1/preview.jpg?at_ms=0&rot=0")
    assert r.status_code == 404
```

Add a `registered_source` fixture to `tests/test_api.py` alongside the existing fixtures: it runs `handle_ingest` on a copy of `sample_video` placed in the library inbox and returns an object exposing `session_id`, `id`, and `dir`.

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_frames.py tests/test_api.py -k "rotation or preview" -v`
Expected: FAIL — `extract_frame() got an unexpected keyword argument 'rotation_deg'`, 404 on the route

- [ ] **Step 3: Implement extract_frame's new arguments**

```python
def extract_frame(
    src: Path,
    dst: Path,
    at_ms: int = 0,
    width: int = 1280,
    rotation_deg: int = 0,
    hwaccel: str | None = None,
    timeout: float = 20.0,
) -> None:
    """Write one frame as a JPEG, rotated `rotation_deg` clockwise.

    Rotation is applied before scaling, so `width` always means the width of
    the upright image. Setup previews read a 4K HEVC original rather than the
    1080p proxy, which is why the decoder can be hardware-accelerated and why
    the timeout is generous.
    """
    vf = ",".join(f for f in (rotation_filter(rotation_deg), f"scale={width}:-2") if f)
    args: list[str] = ["-noautorotate"]
    if hwaccel:
        args += ["-hwaccel", hwaccel]
    args += ["-ss", f"{at_ms / 1000:.3f}", "-i", str(src), "-vf", vf,
             "-frames:v", "1", str(dst)]
    run_ffmpeg(args, timeout=timeout)
```

Keep the existing argument order for `src, dst, at_ms, width` so current call sites are unaffected.

- [ ] **Step 4: Implement the route**

```python
# bootleg/api/routes.py

import threading

FRAME_CACHE_KEEP = 20
# Two concurrent 4K HEVC decodes is what an 8 GB M2 Air absorbs without
# swapping. The wizard's nine-frame grid fires nine requests at once; the
# rest queue here rather than in the kernel's memory pressure handler.
_PREVIEW_SLOTS = threading.Semaphore(2)


def _last_safe_ms(source: sqlite3.Row) -> int:
    """See api_frame: past end-of-stream ffmpeg fails with exit 234, and
    duration - 1ms is not far enough back."""
    fps, duration_ms = source["fps"], source["duration_ms"]
    margin_ms = int(1000 / fps) + 1 if fps > 0 else max(1, duration_ms // 2)
    return max(0, duration_ms - margin_ms)


@router.get("/media/{session_id}/{idx}/preview.jpg")
def api_preview(session_id: str, idx: int, request: Request, at_ms: int = 0, rot: int = 0):
    conn, library = _conn(request), _library(request)
    source = conn.execute(
        "SELECT * FROM sources WHERE session_id = ? AND idx = ?", (session_id, idx)
    ).fetchone()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        rotation_filter(rot)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    src_dir = library.source_dir(session_id, idx)
    original = _original_path(src_dir)
    if original is None:
        raise HTTPException(status_code=404, detail="Original not available")

    at_ms = max(0, min(at_ms, _last_safe_ms(source)))
    dst = src_dir / f"preview-{rot}-{at_ms}.jpg"
    if not dst.exists():
        with _PREVIEW_SLOTS:
            # Re-check inside the semaphore: nine grid requests for the same
            # timestamp would otherwise each spawn their own ffmpeg.
            if not dst.exists():
                try:
                    extract_frame(
                        original, dst, at_ms=at_ms, rotation_deg=rot,
                        hwaccel=detect_accel().hwaccel,
                    )
                except (TranscodeError, ProbeError) as exc:
                    raise HTTPException(
                        status_code=409, detail="Source is still being processed"
                    ) from exc
        _evict_old_frames(src_dir, pattern="preview-*.jpg")
    else:
        os.utime(dst, None)
    return FileResponse(dst, media_type="image/jpeg")
```

Generalize `_evict_old_frames(src_dir, keep=FRAME_CACHE_KEEP)` to `_evict_old_frames(src_dir, pattern="frame-*.jpg", keep=FRAME_CACHE_KEEP)` and update `api_frame`'s call. Have `api_frame` use `_last_safe_ms` instead of its inline copy, keeping its explanatory comment above the helper.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_frames.py tests/test_api.py -v && ruff check .`
Expected: PASS, ruff clean

- [ ] **Step 6: Commit**

```bash
git add bootleg/media/frames.py bootleg/api/routes.py tests/test_frames.py tests/test_api.py
git commit -m "feat(api): serve rotated preview frames from the original"
```

---

### Task 9: setup and source API routes

**Files:**
- Modify: `bootleg/api/routes.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `queue_setup` (Task 7)
- Produces: `GET /api/sources/{source_id}`; `POST /api/sources/{source_id}/setup` with body `{rotation_deg: int, preset_id: str}` returning `{"job_id": str}`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_api.py -- append

def test_get_source_returns_the_row(client, registered_source):
    r = client.get(f"/api/sources/{registered_source.id}")
    assert r.status_code == 200
    assert r.json()["status"] == "needs_setup"
    assert r.json()["rotation_deg"] in (0, 90, 180, 270)


def test_get_source_404s_for_an_unknown_id(client):
    assert client.get("/api/sources/nope").status_code == 404


def test_setup_stores_both_and_queues_a_build(client, registered_source, a_preset):
    r = client.post(
        f"/api/sources/{registered_source.id}/setup",
        json={"rotation_deg": 90, "preset_id": a_preset},
    )
    assert r.status_code == 200
    assert r.json()["job_id"]
    assert client.get(f"/api/sources/{registered_source.id}").json()["rotation_deg"] == 90


def test_setup_rejects_a_non_right_angle(client, registered_source, a_preset):
    r = client.post(
        f"/api/sources/{registered_source.id}/setup",
        json={"rotation_deg": 45, "preset_id": a_preset},
    )
    assert r.status_code == 400


def test_setup_404s_on_an_unknown_preset(client, registered_source):
    r = client.post(
        f"/api/sources/{registered_source.id}/setup",
        json={"rotation_deg": 0, "preset_id": "nope"},
    )
    assert r.status_code == 404


def test_setup_409s_while_a_job_is_running(client, registered_source, a_preset, conn):
    conn.execute(
        "UPDATE sources SET status='detecting' WHERE id=?", (registered_source.id,)
    )
    conn.commit()
    r = client.post(
        f"/api/sources/{registered_source.id}/setup",
        json={"rotation_deg": 0, "preset_id": a_preset},
    )
    assert r.status_code == 409
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_api.py -k setup -v`
Expected: FAIL — 404 for every call, the routes do not exist

- [ ] **Step 3: Implement**

```python
class SetupBody(BaseModel):
    rotation_deg: int
    preset_id: str


@router.get("/api/sources/{source_id}")
def api_get_source(source_id: str, request: Request):
    source = get_source(_conn(request), source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return dict(source)


@router.post("/api/sources/{source_id}/setup")
def api_setup(source_id: str, body: SetupBody, request: Request):
    """Apply a wizard decision: rotation, play region, then rebuild.

    Errors map by kind rather than by message: a bad angle is the caller's
    malformed input (400), a missing row is a 404, and a source with a job
    already running is a conflict the caller can retry (409).
    """
    try:
        job_id = queue_setup(_conn(request), source_id, body.rotation_deg, body.preset_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job_id}
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bootleg/api/routes.py tests/test_api.py
git commit -m "feat(api): add source fetch and setup routes"
```

---

### Task 10: bootleg setup, and doctor reports orientation

**Files:**
- Modify: `bootleg/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `queue_setup` (Task 7)
- Produces: `bootleg setup <source_id> --rotation N [--preset ID] [--now]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py -- append

from bootleg.cli import main


def test_setup_command_queues_a_build(library, registered_source, a_preset, capsys):
    code = main([
        "--library", str(library.root), "setup", registered_source.id,
        "--rotation", "90", "--preset", a_preset,
    ])
    assert code == 0
    assert "queued build_proxy" in capsys.readouterr().out


def test_setup_command_rejects_a_bad_rotation(library, registered_source, a_preset, capsys):
    code = main([
        "--library", str(library.root), "setup", registered_source.id,
        "--rotation", "45", "--preset", a_preset,
    ])
    assert code == 1
    assert "0, 90, 180 or 270" in capsys.readouterr().err


def test_doctor_lists_source_rotation(library, registered_source, capsys):
    main(["--library", str(library.root), "doctor"])
    out = capsys.readouterr().out
    assert "rotation" in out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_cli.py -k "setup or rotation" -v`
Expected: FAIL — `invalid choice: 'setup'`

- [ ] **Step 3: Implement**

```python
def cmd_setup(args) -> int:
    lib = _library(args)
    conn = connect(lib.db_path)
    migrate(conn)
    preset_id = args.preset
    if preset_id is None:
        row = conn.execute(
            "SELECT court_preset_id FROM sources WHERE id=?", (args.source_id,)
        ).fetchone()
        preset_id = row["court_preset_id"] if row else None
        if not preset_id:
            print("no --preset given and none assigned; see `bootleg preset list`",
                  file=sys.stderr)
            return 1
    try:
        job_id = queue_setup(conn, args.source_id, args.rotation, preset_id)
    except (ValueError, LookupError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"queued build_proxy {job_id}")
    if args.now:
        # Two run_once calls: build_proxy, then the detect it enqueued.
        Worker(lib, HANDLERS).run_once()
        Worker(lib, HANDLERS).run_once()
    return 0
```

Register the parser next to the existing ones:

```python
p = sub.add_parser("setup", help="set a source's rotation and play region, then rebuild")
p.add_argument("source_id")
p.add_argument("--rotation", type=int, required=True, help="0, 90, 180 or 270 (clockwise)")
p.add_argument("--preset", help="court preset id; defaults to the one already assigned")
p.add_argument("--now", action="store_true", help="run the jobs inline instead of queueing")
p.set_defaults(func=cmd_setup)
```

Extend `cmd_doctor` with a source table after the existing path lines:

```python
    conn = connect(lib.db_path)
    migrate(conn)
    rows = conn.execute(
        "SELECT session_id, idx, status, width, height, rotation_deg FROM sources"
        " ORDER BY session_id, idx"
    ).fetchall()
    if rows:
        print("sources:")
        for r in rows:
            print(f"  {r['session_id']}/{r['idx']:02d}  {r['status']:<12}"
                  f"  {r['width']}x{r['height']}  rotation {r['rotation_deg']}")
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_cli.py -v && ruff check . && pytest -q`
Expected: PASS, ruff clean, whole Python suite green

- [ ] **Step 5: Commit**

```bash
git add bootleg/cli.py tests/test_cli.py
git commit -m "feat(cli): add bootleg setup and report rotation in doctor"
```

---

### Task 11: rebuild the real session and confirm detection recovers

The backend is now complete without any UI. Run the actual footage through it before building the wizard — if rotation was not the whole story, that changes what the wizard needs to expose.

**Files:** none (operational task; findings recorded in the commit message)

- [ ] **Step 1: Assign a play region to the existing source**

The 608x1080 proxy is unusable for drawing a region, so create the quad from the CLI using the trapezoid the UI defaults to, then refine it in the wizard later:

```bash
conda run -n bootleg bootleg --library /Volumes/SanDisk_2TB/BootlegVision \
  preset add --name "2026-08-19 source 1" --quad "0.35,0.35 0.65,0.35 0.98,1.0 0.02,1.0"
```

- [ ] **Step 2: Rebuild the proxy landscape and re-detect**

```bash
conda run -n bootleg bootleg --library /Volumes/SanDisk_2TB/BootlegVision \
  setup 0926ad87101b40dc9cd63a915858147c --rotation 0 --preset <preset_id_from_step_1> --now
```

Expected: ~7 minutes of transcode, then ~5 minutes of detection.

- [ ] **Step 3: Verify the proxy orientation**

Run: `ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 /Volumes/SanDisk_2TB/BootlegVision/sessions/2026-08-19/sources/01/proxy.mp4`
Expected: `1920,1080` — not `608,1080`

- [ ] **Step 4: Compare detection against the recorded baseline**

```bash
python3 - <<'EOF'
import json
p = "/Volumes/SanDisk_2TB/BootlegVision/sessions/2026-08-19/sources/01/features.jsonl"
rows = [json.loads(l) for l in open(p)]
print("frames", len(rows))
print("n>=1", sum(1 for d in rows if d["n"] >= 1))
print("n>=2", sum(1 for d in rows if d["n"] >= 2))
EOF
sqlite3 /Volumes/SanDisk_2TB/BootlegVision/library.db "SELECT COUNT(*) FROM rallies;"
```

Baseline from the portrait proxy, for comparison: 5875 frames, `n>=1` 668, `n>=2` 55, 0 rallies. A landscape proxy that still produces `n>=2` in under 5% of frames means the play region or the camera angle is the problem, not orientation — stop and re-diagnose before continuing to the UI tasks.

- [ ] **Step 5: Record the finding**

```bash
git commit --allow-empty -m "chore: record post-rotation detection numbers for 2026-08-19/01"
```

Put the actual frame counts and rally count in the commit body.

---

### Task 12: previewTimestamps and the setup route

**Files:**
- Create: `web/src/lib/preview.ts`, `web/tests/preview.test.ts`
- Modify: `web/src/lib/router.svelte.ts`, `web/tests/router.test.ts`, `web/src/lib/types.ts`, `web/src/lib/api.ts`

**Interfaces:**
- Produces: `previewTimestamps(durationMs: number, fps: number, n?: number): number[]`; `Route` gains `{name: 'setup'; id: string}`; `api.getSource`, `api.setup`, `api.previewUrl`

- [ ] **Step 1: Write the failing tests**

```typescript
// web/tests/preview.test.ts -- new file
import { describe, expect, it } from 'vitest'
import { previewTimestamps } from '../src/lib/preview'

describe('previewTimestamps', () => {
  it('spaces nine frames across the clip at 10% intervals', () => {
    const ts = previewTimestamps(1_000_000, 30)
    expect(ts).toHaveLength(9)
    expect(ts[0]).toBe(100_000)
    expect(ts[8]).toBe(900_000)
  })

  it('never returns a timestamp past the last decodable frame', () => {
    const ts = previewTimestamps(2000, 30)
    expect(Math.max(...ts)).toBeLessThanOrEqual(1966)
  })

  it('de-duplicates on a clip too short to spread across', () => {
    expect(new Set(previewTimestamps(10, 30)).size).toBe(previewTimestamps(10, 30).length)
  })

  it('honours an explicit count', () => {
    expect(previewTimestamps(1_000_000, 30, 4)).toHaveLength(4)
  })
})
```

```typescript
// web/tests/router.test.ts -- append
it('parses the setup route', () => {
  expect(parseHash('#/setup/abc123')).toEqual({ name: 'setup', id: 'abc123' })
})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npx vitest run tests/preview.test.ts tests/router.test.ts`
Expected: FAIL — cannot resolve `../src/lib/preview`

- [ ] **Step 3: Implement**

```typescript
// web/src/lib/preview.ts
import { clamp, lastSafeFrameMs } from './time'

/**
 * Timestamps for the setup grid: evenly spread across the clip rather than
 * randomly sampled. Even spacing is reproducible across reloads and visibly
 * covers the whole session; random sampling only appears to.
 *
 * Never includes t=0 -- the frame a phone records as the shutter is hit is
 * routinely black, which is the reason this grid exists.
 */
export function previewTimestamps(durationMs: number, fps: number, n = 9): number[] {
  const max = lastSafeFrameMs(durationMs, fps)
  const out: number[] = []
  for (let i = 1; i <= n; i++) {
    out.push(Math.round(clamp((durationMs * i) / (n + 1), 0, max)))
  }
  return [...new Set(out)]
}
```

```typescript
// web/src/lib/router.svelte.ts
export type Route =
  | { name: 'library' }
  | { name: 'session'; id: string }
  | { name: 'setup'; id: string }

// inside parseHash, before the session branch:
if (parts.length === 2 && parts[0] === 'setup') {
  return { name: 'setup', id: parts[1] }
}
```

```typescript
// web/src/lib/api.ts -- add to the exported object
  getSource: (id: string) => req<Source>(`/api/sources/${id}`),
  setup: (id: string, rotation_deg: number, preset_id: string) =>
    post<{ job_id: string }>(`/api/sources/${id}/setup`, { rotation_deg, preset_id }),
  previewUrl: (sessionId: string, idx: number, atMs: number, rot: number) =>
    `/media/${sessionId}/${idx}/preview.jpg?at_ms=${atMs}&rot=${rot}`,
```

Add `rotation_deg: number` to `interface Source` in `web/src/lib/types.ts`.

- [ ] **Step 4: Run the tests**

Run: `cd web && npx vitest run && npx svelte-check --threshold warning`
Expected: PASS, 0 errors

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/preview.ts web/src/lib/router.svelte.ts web/src/lib/api.ts \
        web/src/lib/types.ts web/tests/preview.test.ts web/tests/router.test.ts
git commit -m "feat(web): add preview sampling, the setup route and its API calls"
```

---

### Task 13: extract QuadCanvas out of QuadEditor

A pure refactor: the existing `quad-editor-scrub.test.ts` and every quad test must stay green without modification, which is the proof that nothing moved but the file boundary.

**Files:**
- Create: `web/src/components/QuadCanvas.svelte`
- Modify: `web/src/components/QuadEditor.svelte`

**Interfaces:**
- Produces: `QuadCanvas` props `{ frameSrc: string, timeMs: number, maxMs: number, fps: number, points: [number, number][], onpoints: (p: [number, number][]) => void, onseek: (ms: number) => void, alt: string }`

- [ ] **Step 1: Create QuadCanvas with the drag surface and scrub controls**

Move, verbatim, out of `QuadEditor.svelte`: `at()`, `onHandleDown`, `onHandleMove`, `endDrag`, the `wrap` binding, the `<img>`, the clip-path overlay, the four handle buttons, the scrub button row, and the `frameError` message. The component owns no state beyond `dragging`, `wrap`, and `frameError`; `points` and `timeMs` arrive as props and changes go out through `onpoints` / `onseek`. Keep every explanatory comment with the code it explains — particularly the two long ones about `overflow-hidden` clipping the y=1.0 handles and about why the wrapper has no fixed aspect ratio.

- [ ] **Step 2: Reduce QuadEditor to the panel**

`QuadEditor` keeps `sourceId`, `points`, `name`, `presets`, `busy`, `save()`, `assignExisting()`, `openAt()`, `frameMs`/`scrubMs`, `maxMs`, and renders:

```svelte
<QuadCanvas
  frameSrc={api.frameUrl(sessionId, source.idx, frameMs)}
  timeMs={scrubMs}
  maxMs={maxMs}
  fps={source.fps}
  points={points}
  onpoints={(p) => (points = p)}
  onseek={seek}
  alt="source {source.idx} at {formatTs(frameMs)}"
/>
```

- [ ] **Step 3: Run the existing tests unchanged**

Run: `cd web && npx vitest run tests/quad-editor-scrub.test.ts tests/quad.test.ts tests/resegment-panel.test.ts`
Expected: PASS with no edits to those files. If a test needs changing, the extraction changed behaviour — fix the component, not the test.

- [ ] **Step 4: Full check**

Run: `cd web && npx vitest run && npx svelte-check --threshold warning`
Expected: PASS, 0 errors

- [ ] **Step 5: Commit**

```bash
git add web/src/components/QuadCanvas.svelte web/src/components/QuadEditor.svelte
git commit -m "refactor(web): extract QuadCanvas so the wizard can reuse it"
```

---

### Task 14: the setup wizard

**Files:**
- Create: `web/src/routes/Setup.svelte`, `web/tests/setup-wizard.test.ts`
- Modify: `web/src/App.svelte`

**Interfaces:**
- Consumes: `previewTimestamps`, `api.getSource`, `api.previewUrl`, `api.setup`, `api.listPresets`, `api.createPreset`, `QuadCanvas` (Tasks 12–13)

- [ ] **Step 1: Write the failing tests**

```typescript
// web/tests/setup-wizard.test.ts -- new file
import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const source = {
  id: 'src1', session_id: 's1', idx: 1, recorded_at: '2026-08-19T10:00:00Z',
  offset_ms: 0, duration_ms: 1173905, width: 3840, height: 2160, fps: 30,
  has_original: 1, court_preset_id: null, status: 'needs_setup', rotation_deg: 90,
}

const mockApi = {
  getSource: vi.fn().mockResolvedValue(source),
  listPresets: vi.fn().mockResolvedValue([]),
  createPreset: vi.fn().mockResolvedValue({ id: 'p1' }),
  setup: vi.fn().mockResolvedValue({ job_id: 'j1' }),
  previewUrl: (s: string, i: number, at: number, rot: number) =>
    `/media/${s}/${i}/preview.jpg?at_ms=${at}&rot=${rot}`,
}
vi.mock('../src/lib/api', () => ({ api: mockApi }))

const { default: Setup } = await import('../src/routes/Setup.svelte')

describe('Setup wizard', () => {
  let target: HTMLDivElement
  let instance: unknown

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getSource.mockResolvedValue(source)
    mockApi.listPresets.mockResolvedValue([])
    mockApi.createPreset.mockResolvedValue({ id: 'p1' })
    mockApi.setup.mockResolvedValue({ job_id: 'j1' })
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
  })

  async function open() {
    instance = mount(Setup, { target, props: { id: 'src1' } })
    flushSync()
    await vi.waitFor(() => expect(mockApi.getSource).toHaveBeenCalled())
    flushSync()
  }

  function grid(): HTMLImageElement[] {
    return [...target.querySelectorAll('img[data-grid]')] as HTMLImageElement[]
  }

  function click(label: string) {
    const el = target.querySelector(`button[aria-label="${label}"]`) as HTMLButtonElement
    if (!el) throw new Error(`${label} not found`)
    el.click()
    flushSync()
  }

  it('shows nine spread preview frames, none of them t=0', async () => {
    await open()
    expect(grid()).toHaveLength(9)
    expect(grid().every((img) => !img.src.includes('at_ms=0&'))).toBe(true)
  })

  it('opens at the rotation the source was ingested with', async () => {
    await open()
    expect(grid()[0].src).toContain('rot=90')
  })

  it('rotating clockwise re-requests the grid at the next quarter turn', async () => {
    await open()
    click('rotate clockwise')
    expect(grid()[0].src).toContain('rot=180')
  })

  it('rotating counter-clockwise wraps below zero', async () => {
    await open()
    click('rotate counter-clockwise')  // 90 -> 0
    click('rotate counter-clockwise')  // 0 -> 270
    expect(grid()[0].src).toContain('rot=270')
  })

  it('cannot start detection before a play region is set', async () => {
    await open()
    const start = target.querySelector('button[aria-label="start detection"]')
    expect((start as HTMLButtonElement).disabled).toBe(true)
  })

  it('creates the preset and posts the setup exactly once', async () => {
    await open()
    click('rotate counter-clockwise')       // 90 -> 0
    click('use default play region')
    click('start detection')
    click('start detection')                // double-click must not double-post
    await vi.waitFor(() => expect(mockApi.setup).toHaveBeenCalledTimes(1))
    expect(mockApi.createPreset).toHaveBeenCalledTimes(1)
    expect(mockApi.setup).toHaveBeenCalledWith('src1', 0, 'p1')
  })
})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npx vitest run tests/setup-wizard.test.ts`
Expected: FAIL — cannot resolve `../src/routes/Setup.svelte`

- [ ] **Step 3: Implement the wizard**

```svelte
<script lang="ts">
  import QuadCanvas from '../components/QuadCanvas.svelte'
  import { api } from '../lib/api'
  import { previewTimestamps } from '../lib/preview'
  import { DEFAULT_QUAD_POINTS, clonePoints, defaultPresetName } from '../lib/quad'
  import { clamp, formatTs, frameStep, lastSafeFrameMs } from '../lib/time'
  import type { Preset, Source } from '../lib/types'

  let { id }: { id: string } = $props()

  let source = $state<Source | null>(null)
  let presets = $state<Preset[]>([])
  let rotation = $state(0)
  let workingMs = $state(0)
  let scrubMs = $state(0)
  let points = $state<[number, number][] | null>(null)   // null until the user commits one
  let busy = $state(false)
  let error = $state<string | null>(null)

  const timestamps = $derived(
    source ? previewTimestamps(source.duration_ms, source.fps) : [],
  )
  const maxMs = $derived(source ? lastSafeFrameMs(source.duration_ms, source.fps) : 0)

  $effect(() => {
    void (async () => {
      try {
        const s = await api.getSource(id)
        source = s
        rotation = s.rotation_deg
        // The working frame starts at the first grid timestamp rather than
        // t=0 for the same reason the grid skips it: phone footage opens on
        // a black frame more often than not.
        workingMs = previewTimestamps(s.duration_ms, s.fps)[0] ?? 0
        scrubMs = workingMs
        presets = await api.listPresets()
      } catch (e) {
        error = String(e)
      }
    })()
  })

  function rotate(dir: 1 | -1) {
    rotation = (rotation + dir * 90 + 360) % 360
  }

  function seek(ms: number) {
    const next = Math.round(clamp(ms, 0, maxMs))
    workingMs = next
    scrubMs = next
  }

  async function start() {
    if (!source || !points || busy) return
    busy = true
    error = null
    try {
      const created = await api.createPreset(
        defaultPresetName(source.session_id, source.idx), points,
      )
      await api.setup(source.id, rotation, created.id)
      window.location.hash = `/s/${source.session_id}`
    } catch (e) {
      error = String(e)
    } finally {
      busy = false
    }
  }
</script>
```

Markup requirements the tests pin down:

- Nine `<img data-grid src={api.previewUrl(source.session_id, source.idx, t, rotation)}>`, each in a button that calls `seek(t)`.
- Buttons `aria-label="rotate counter-clockwise"` and `aria-label="rotate clockwise"` calling `rotate(-1)` / `rotate(1)`.
- A button `aria-label="use default play region"` setting `points = clonePoints(DEFAULT_QUAD_POINTS)`, plus one button per existing preset that loads its points.
- `QuadCanvas` bound to the working frame, `onpoints={(p) => (points = p)}`, `onseek={seek}`.
- A button `aria-label="start detection"`, `disabled={!points || busy}`, calling `start()`.
- A warning line, shown only when `source.status === 'ready'`: "Re-running setup rebuilds the proxy and replaces this source's rallies, including any boundaries you hand-edited."

Wire the route in `App.svelte`:

```svelte
{:else if router.current.name === 'setup'}
  <Setup id={router.current.id} />
```

- [ ] **Step 4: Run the tests**

Run: `cd web && npx vitest run && npx svelte-check --threshold warning`
Expected: PASS, 0 errors

- [ ] **Step 5: Commit**

```bash
git add web/src/routes/Setup.svelte web/src/App.svelte web/tests/setup-wizard.test.ts
git commit -m "feat(web): add the setup wizard"
```

---

### Task 15: surface sources that need setup

**Files:**
- Modify: `web/src/routes/Library.svelte`, `web/src/routes/Session.svelte`
- Test: `web/tests/library-setup-card.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// web/tests/library-setup-card.test.ts -- new file
import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mockApi = {
  listSessions: vi.fn().mockResolvedValue([
    { id: 's1', played_on: '2026-08-19', status: 'needs_setup',
      source_count: 1, rally_count: 0, starred_count: 0 },
  ]),
}
vi.mock('../src/lib/api', () => ({ api: mockApi }))

const { default: Library } = await import('../src/routes/Library.svelte')

describe('Library', () => {
  let target: HTMLDivElement
  let instance: unknown

  beforeEach(() => {
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
  })

  it('links a needs_setup session to its wizard instead of its review page', async () => {
    instance = mount(Library, { target, props: {} })
    flushSync()
    await vi.waitFor(() => expect(mockApi.listSessions).toHaveBeenCalled())
    flushSync()
    const link = target.querySelector('a[href^="#/setup/"], button[aria-label="set up"]')
    expect(link).not.toBeNull()
  })
})
```

Adjust the mocked session shape to whatever `listSessions` actually returns in `web/src/lib/types.ts` — read it first rather than assuming.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web && npx vitest run tests/library-setup-card.test.ts`
Expected: FAIL — no setup affordance rendered

- [ ] **Step 3: Implement**

In `Library.svelte`, a session whose status is `needs_setup` renders a "Set up" affordance linking to `#/setup/<first source id>` rather than the normal review link. In `Session.svelte`, render the same card above the rally list for each source in `needs_setup`, and hide the `QuadEditor` panel for those sources (they have no proxy, so `frame.jpg` would 404).

- [ ] **Step 4: Run the tests**

Run: `cd web && npx vitest run && npx svelte-check --threshold warning`
Expected: PASS, 0 errors

- [ ] **Step 5: Commit**

```bash
git add web/src/routes/Library.svelte web/src/routes/Session.svelte \
        web/tests/library-setup-card.test.ts
git commit -m "feat(web): surface sources waiting on setup"
```

---

### Task 16: end-to-end pass on real footage

**Files:** `docs/superpowers/specs/2026-08-20-import-setup-wizard-design.md` (status line only)

- [ ] **Step 1: Build the SPA and start the server**

```bash
cd web && npm run build && cd .. && conda run -n bootleg bootleg --library /Volumes/SanDisk_2TB/BootlegVision serve
```

- [ ] **Step 2: Drop a real clip into the inbox and watch it register**

Expected: the source appears in the Library with a "Set up" affordance within seconds — no transcode wait.

- [ ] **Step 3: Walk the wizard**

Confirm: the grid shows nine non-black frames; rotating flips all nine; clicking a frame promotes it; the scrubber steps frame by frame; "start detection" is disabled until a region is drawn.

- [ ] **Step 4: Confirm the pipeline runs to completion**

Run: `sqlite3 /Volumes/SanDisk_2TB/BootlegVision/library.db "SELECT idx,status,width,height,rotation_deg FROM sources;"`
Expected: `ready`, landscape dimensions, the rotation chosen in the wizard.

- [ ] **Step 5: Full suite and status flip**

Run: `pytest -q && ruff check . && cd web && npx vitest run && npx svelte-check --threshold warning`
Expected: all green.

Then set the spec's status line to `Implemented` and commit:

```bash
git add docs/superpowers/specs/2026-08-20-import-setup-wizard-design.md
git commit -m "docs: mark the import setup wizard spec implemented"
```

---

## Self-Review Notes

**Spec coverage:** §3 pipeline → Tasks 5, 6. §4 rotation → Tasks 2, 3, 4. §5 preview → Task 8, 12. §6 wizard → Tasks 13, 14, 15. §7 API → Tasks 8, 9. §8 CLI → Task 10. §9 existing data → Tasks 4 (migration), 11 (rebuild). §10 failure handling → Tasks 6, 8, 9. §11 testing → distributed through every task. §12 deferred → not implemented, by design.

**Known ordering constraint:** Task 11 runs the real footage through a backend that has no wizard yet, using `bootleg preset add` for the quad. That is deliberate — it validates the rotation fix a full day of UI work before the UI exists, and if detection does not recover, Tasks 12–16 are the wrong next thing to build.
