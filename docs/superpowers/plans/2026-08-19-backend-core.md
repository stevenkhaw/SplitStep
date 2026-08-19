# BootlegVision Backend Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A headless Python backend that ingests tennis footage from a watched folder, extracts visual and audio features, segments them into rallies, and serves everything over a REST API — with no frontend.

**Architecture:** One process. A FastAPI app plus a worker thread that claims jobs from a SQLite table. Detection is split into an expensive cached stage (`features.jsonl`) and a cheap pure-function stage (`segment()`), so retuning thresholds costs milliseconds. All hardware differences funnel through one `accel` module.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, stdlib `sqlite3`, numpy, scipy, ultralytics (YOLO11), opencv-python-headless, watchdog, ffmpeg (subprocess), pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-08-19-bootlegvision-design.md`

## Global Constraints

- Python **3.12** exactly. Create with `conda create -n bootleg python=3.12`. Do not use 3.13 — torch wheels are unreliable there.
- ffmpeg must be on `PATH`. Install with `brew install ffmpeg`.
- SQLite opened with `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=FULL`. Both, always.
- Explicit SQL only. **No ORM.** Migrations are numbered `.sql` files.
- The library root must be mounted and writable at startup. **Never create the directory tree automatically** — a missing root is an error, not a signal to initialize.
- Clip encode profile is locked and must never change: `mp4 · H.264 High · yuv420p · 3840×2160 · 30 fps CFR · CRF 20 · AAC 128k 48kHz stereo`.
- Proxy video is **H.264, not HEVC**, with `-g 30`.
- Frame sampling for detection is **5 fps**. Audio grid is the same 5 Hz.
- Segmentation defaults: `min_duration_s=1.5`, `close_gap_s=1.5`, `pad_start_s=0.3`, `pad_end_s=0.5`. The 1.5 s floor exists to preserve aces; do not raise it.
- **No YOLO inference inside tests.** Detector output is always mocked or fixtured.
- `det_start_ms` / `det_end_ms` are written once at creation and never updated.
- Every ffmpeg subprocess call captures stderr and raises on non-zero exit.

---

## File Structure

```
pyproject.toml
bootleg/
  __init__.py
  config.py            Library — root paths, mount validation
  accel.py             host detection → ffmpeg flags + torch device
  db/
    __init__.py
    schema.py          connect(), migrate()
    migrations/001_init.sql
    sessions.py        session + source rows
    rallies.py         rally rows
    jobs.py            job queue primitives
  media/
    __init__.py
    probe.py           ffprobe wrapper
    transcode.py       proxy, thumbnails
  detect/
    __init__.py
    features.py        FeatureFrame / Player types, JSONL round-trip
    geometry.py        Quad, point-in-polygon
    audio.py           PCM → hits → 5 Hz grid
    vision.py          detections → FeatureFrames; YOLO runner
    segment.py         pure: features + params → intervals
  jobs/
    __init__.py
    worker.py          claim/run/heartbeat loop
    handlers.py        ingest, detect
  watcher.py           inbox watchdog with size-stability check
  api/
    __init__.py
    app.py             FastAPI factory
    media.py           HTTP range responses
    routes.py          sessions, rallies, jobs
  cli.py               serve / ingest / detect / segment
tests/
  conftest.py
  fixtures/
  test_config.py
  test_db.py
  test_probe.py
  test_features.py
  test_geometry.py
  test_segment.py
  test_audio.py
  test_vision.py
  test_transcode.py
  test_jobs.py
  test_handlers.py
  test_watcher.py
  test_api.py
```

Split by responsibility, not layer. `detect/` holds everything that turns pixels and samples into rally boundaries; `media/` holds everything that shells out to ffmpeg; `db/` holds SQL.

---

### Task 1: Project scaffold and library root

**Files:**
- Create: `pyproject.toml`
- Create: `bootleg/__init__.py`
- Create: `bootleg/config.py`
- Test: `tests/test_config.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Library` (frozen dataclass) with `.root`, `.db_path`, `.inbox`, `.sessions_dir`, `.reels_dir`, `.session_dir(session_id) -> Path`, `.source_dir(session_id, idx) -> Path`, `.clips_dir(session_id) -> Path`, `.free_bytes() -> int`, `.require_free(need_bytes: int) -> None`; classmethod `Library.open(root: Path) -> Library`; exceptions `LibraryNotMounted`, `NotEnoughSpace`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "bootleg"
version = "0.1.0"
requires-python = "==3.12.*"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "numpy>=2.0",
    "scipy>=1.14",
    "ultralytics>=8.3",
    "opencv-python-headless>=4.10",
    "watchdog>=5.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "httpx>=0.27", "ruff>=0.7"]

[project.scripts]
bootleg = "bootleg.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_config.py`:

```python
import pytest
from pathlib import Path
from bootleg.config import Library, LibraryNotMounted, NotEnoughSpace


def test_open_returns_library_for_existing_writable_root(tmp_path):
    lib = Library.open(tmp_path)
    assert lib.root == tmp_path
    assert lib.db_path == tmp_path / "library.db"
    assert lib.inbox == tmp_path / "_inbox"
    assert lib.sessions_dir == tmp_path / "sessions"
    assert lib.reels_dir == tmp_path / "reels"


def test_open_raises_when_root_missing(tmp_path):
    missing = tmp_path / "not-plugged-in"
    with pytest.raises(LibraryNotMounted) as exc:
        Library.open(missing)
    assert str(missing) in str(exc.value)


def test_open_does_not_create_the_root(tmp_path):
    missing = tmp_path / "not-plugged-in"
    with pytest.raises(LibraryNotMounted):
        Library.open(missing)
    assert not missing.exists()


def test_source_dir_is_zero_padded(tmp_path):
    lib = Library.open(tmp_path)
    assert lib.source_dir("2026-08-19", 1) == tmp_path / "sessions" / "2026-08-19" / "sources" / "01"
    assert lib.source_dir("2026-08-19", 12) == tmp_path / "sessions" / "2026-08-19" / "sources" / "12"


def test_free_bytes_is_positive(tmp_path):
    assert Library.open(tmp_path).free_bytes() > 0


def test_require_free_passes_for_a_small_request(tmp_path):
    Library.open(tmp_path).require_free(1024)  # must not raise


def test_require_free_raises_before_a_write_that_cannot_fit(tmp_path):
    lib = Library.open(tmp_path)
    with pytest.raises(NotEnoughSpace) as exc:
        lib.require_free(lib.free_bytes() + 10**12)
    assert "space" in str(exc.value).lower()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bootleg.config'`

- [ ] **Step 4: Write the implementation**

Create `bootleg/__init__.py` (empty file).

Create `bootleg/config.py`:

```python
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


class LibraryNotMounted(Exception):
    """The library root is absent or not writable."""


class NotEnoughSpace(Exception):
    """The library volume cannot hold the pending write."""


@dataclass(frozen=True)
class Library:
    root: Path

    @classmethod
    def open(cls, root: Path) -> "Library":
        root = Path(root)
        if not root.is_dir():
            raise LibraryNotMounted(
                f"Library root not found: {root}. Is the drive plugged in?"
            )
        if not os.access(root, os.W_OK):
            raise LibraryNotMounted(f"Library root is not writable: {root}")
        return cls(root=root)

    @property
    def db_path(self) -> Path:
        return self.root / "library.db"

    @property
    def inbox(self) -> Path:
        return self.root / "_inbox"

    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    @property
    def reels_dir(self) -> Path:
        return self.root / "reels"

    def session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id

    def source_dir(self, session_id: str, idx: int) -> Path:
        return self.session_dir(session_id) / "sources" / f"{idx:02d}"

    def clips_dir(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "clips"

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.root).free

    def require_free(self, need_bytes: int) -> None:
        """Refuse to start a write that cannot finish.

        A 4K clip that dies at 90% is worse than a job that never starts.
        """
        free = self.free_bytes()
        if free < need_bytes:
            raise NotEnoughSpace(
                f"Need {need_bytes / 1e9:.1f} GB of free space on {self.root}, "
                f"only {free / 1e9:.1f} GB available."
            )
```

Create `tests/conftest.py`:

```python
import pytest
from pathlib import Path
from bootleg.config import Library


@pytest.fixture
def library(tmp_path) -> Library:
    for sub in ("_inbox", "sessions", "reels"):
        (tmp_path / sub).mkdir()
    return Library.open(tmp_path)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml bootleg/__init__.py bootleg/config.py tests/conftest.py tests/test_config.py
git commit -m "feat: add library root config with mount validation"
```

---

### Task 2: Database schema and migrations

**Files:**
- Create: `bootleg/db/__init__.py`
- Create: `bootleg/db/schema.py`
- Create: `bootleg/db/migrations/001_init.sql`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `Library` from Task 1
- Produces: `connect(db_path: Path) -> sqlite3.Connection` (row_factory set to `sqlite3.Row`, WAL + `synchronous=FULL` + foreign keys on); `migrate(conn) -> int` returning the resulting schema version

- [ ] **Step 1: Write the failing test**

Create `tests/test_db.py`:

```python
from bootleg.db.schema import connect, migrate


def test_connect_sets_wal_and_full_sync(library):
    conn = connect(library.db_path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrate_creates_all_tables(library):
    conn = connect(library.db_path)
    migrate(conn)
    names = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "sessions", "sources", "rallies", "court_presets",
        "reels", "reel_items", "jobs",
    } <= names


def test_migrate_is_idempotent(library):
    conn = connect(library.db_path)
    assert migrate(conn) == 1
    assert migrate(conn) == 1


def test_rally_cascades_when_source_deleted(library):
    conn = connect(library.db_path)
    migrate(conn)
    conn.execute(
        "INSERT INTO sessions (id,title,played_on,status,created_at)"
        " VALUES ('s1','t','2026-08-19','ready','now')"
    )
    conn.execute(
        "INSERT INTO sources (id,session_id,idx,recorded_at,offset_ms,duration_ms,"
        "width,height,fps,status) VALUES ('src1','s1',1,'now',0,1000,1920,1080,30,'ready')"
    )
    conn.execute(
        "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
        "det_start_ms,det_end_ms,confidence) VALUES ('r1','s1','src1',1,0,100,0,100,0.9)"
    )
    conn.commit()
    conn.execute("DELETE FROM sources WHERE id='src1'")
    conn.commit()
    assert conn.execute("SELECT count(*) FROM rallies").fetchone()[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bootleg.db'`

- [ ] **Step 3: Write the migration SQL**

Create `bootleg/db/migrations/001_init.sql`:

```sql
CREATE TABLE sessions (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  played_on   TEXT NOT NULL,
  status      TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE sources (
  id              TEXT PRIMARY KEY,
  session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  idx             INTEGER NOT NULL,
  recorded_at     TEXT NOT NULL,
  offset_ms       INTEGER NOT NULL,
  duration_ms     INTEGER NOT NULL,
  width           INTEGER NOT NULL,
  height          INTEGER NOT NULL,
  fps             REAL    NOT NULL,
  original_name   TEXT,
  has_original    INTEGER NOT NULL DEFAULT 1,
  court_preset_id TEXT REFERENCES court_presets(id),
  status          TEXT NOT NULL,
  UNIQUE(session_id, idx)
);

CREATE TABLE rallies (
  id            TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  source_id     TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  idx           INTEGER NOT NULL,
  start_ms      INTEGER NOT NULL,
  end_ms        INTEGER NOT NULL,
  det_start_ms  INTEGER NOT NULL,
  det_end_ms    INTEGER NOT NULL,
  confidence    REAL    NOT NULL,
  starred       INTEGER NOT NULL DEFAULT 0,
  rejected      INTEGER NOT NULL DEFAULT 0,
  reviewed_at   TEXT,
  clip_path     TEXT,
  UNIQUE(session_id, idx)
);

CREATE TABLE court_presets (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  quad       TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE reels (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  slug          TEXT NOT NULL UNIQUE,
  rendered_path TEXT,
  rendered_at   TEXT,
  dirty         INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT NOT NULL
);

CREATE TABLE reel_items (
  reel_id  TEXT NOT NULL REFERENCES reels(id) ON DELETE CASCADE,
  rally_id TEXT NOT NULL REFERENCES rallies(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  PRIMARY KEY (reel_id, rally_id)
);

CREATE TABLE jobs (
  id           TEXT PRIMARY KEY,
  type         TEXT NOT NULL,
  payload      TEXT NOT NULL,
  status       TEXT NOT NULL,
  progress     REAL NOT NULL DEFAULT 0,
  error        TEXT,
  heartbeat_at TEXT,
  created_at   TEXT NOT NULL,
  finished_at  TEXT
);

CREATE INDEX idx_rallies_session ON rallies(session_id, idx);
CREATE INDEX idx_rallies_source  ON rallies(source_id);
CREATE INDEX idx_rallies_starred ON rallies(starred) WHERE starred = 1;
CREATE INDEX idx_sources_session ON sources(session_id, idx);
CREATE INDEX idx_jobs_queued     ON jobs(status, created_at);
```

- [ ] **Step 4: Write the schema module**

Create `bootleg/db/__init__.py` (empty file).

Create `bootleg/db/schema.py`:

```python
import sqlite3
from pathlib import Path

MIGRATIONS = Path(__file__).parent / "migrations"


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> int:
    version = _current_version(conn)
    for path in sorted(MIGRATIONS.glob("*.sql")):
        n = int(path.name.split("_", 1)[0])
        if n <= version:
            continue
        conn.executescript(path.read_text())
        conn.execute(f"PRAGMA user_version={n}")
        conn.commit()
        version = n
    return version
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_db.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add bootleg/db tests/test_db.py
git commit -m "feat: add sqlite schema and migration runner"
```

---

### Task 3: Hardware acceleration and ffprobe

**Files:**
- Create: `bootleg/accel.py`
- Create: `bootleg/media/__init__.py`
- Create: `bootleg/media/probe.py`
- Test: `tests/test_probe.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Accel` dataclass with `.hwaccel: str | None`, `.h264_encoder: str`, `.torch_device: str`; `detect_accel() -> Accel`. `probe(path: Path) -> MediaInfo` where `MediaInfo` has `.duration_ms: int`, `.width: int`, `.height: int`, `.fps: float`, `.recorded_at: str | None`, `.has_audio: bool`; exception `ProbeError`

- [ ] **Step 1: Write the failing test**

Create `tests/test_probe.py`:

```python
import subprocess
import pytest
from bootleg.accel import detect_accel
from bootleg.media.probe import probe, ProbeError


@pytest.fixture
def sample_video(tmp_path):
    """2 second 320x240 30fps clip with a 440Hz tone."""
    out = tmp_path / "sample.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_detect_accel_returns_usable_values():
    a = detect_accel()
    assert a.h264_encoder
    assert a.torch_device in {"cuda", "mps", "cpu"}


def test_probe_reads_dimensions_and_duration(sample_video):
    info = probe(sample_video)
    assert info.width == 320
    assert info.height == 240
    assert 1900 <= info.duration_ms <= 2100
    assert 29.0 <= info.fps <= 31.0
    assert info.has_audio is True


def test_probe_raises_on_non_media(tmp_path):
    junk = tmp_path / "notavideo.mp4"
    junk.write_bytes(b"this is not a video")
    with pytest.raises(ProbeError):
        probe(junk)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_probe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bootleg.accel'`

- [ ] **Step 3: Write `bootleg/accel.py`**

```python
import platform
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Accel:
    hwaccel: str | None
    h264_encoder: str
    torch_device: str


def _ffmpeg_encoders() -> str:
    try:
        return subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


@lru_cache(maxsize=1)
def detect_accel() -> Accel:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH. Install it: brew install ffmpeg")

    encoders = _ffmpeg_encoders()

    if platform.system() == "Darwin" and "h264_videotoolbox" in encoders:
        return Accel("videotoolbox", "h264_videotoolbox", _torch_device())
    if "h264_nvenc" in encoders and _has_cuda():
        return Accel("cuda", "h264_nvenc", "cuda")
    if "h264_qsv" in encoders:
        return Accel("qsv", "h264_qsv", "cpu")
    return Accel(None, "libx264", _torch_device())


def _has_cuda() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def _torch_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"
```

- [ ] **Step 4: Write `bootleg/media/probe.py`**

Create `bootleg/media/__init__.py` (empty file).

```python
import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


class ProbeError(Exception):
    """ffprobe could not read the file."""


@dataclass(frozen=True)
class MediaInfo:
    duration_ms: int
    width: int
    height: int
    fps: float
    recorded_at: str | None
    has_audio: bool


def probe(path: Path) -> MediaInfo:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise ProbeError(f"ffprobe failed for {path}: {proc.stderr.strip()}")

    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError(f"No video stream in {path}")

    fmt = data.get("format", {})
    duration_s = float(fmt.get("duration") or video.get("duration") or 0.0)
    if duration_s <= 0:
        raise ProbeError(f"Could not determine duration for {path}")

    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    fps = float(Fraction(rate)) if not rate.startswith("0/") else 0.0

    tags = fmt.get("tags", {})
    recorded_at = tags.get("creation_time")

    return MediaInfo(
        duration_ms=int(round(duration_s * 1000)),
        width=int(video["width"]),
        height=int(video["height"]),
        fps=fps,
        recorded_at=recorded_at,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_probe.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add bootleg/accel.py bootleg/media tests/test_probe.py
git commit -m "feat: add hardware accel detection and ffprobe wrapper"
```

---

### Task 4: Feature types and geometry

**Files:**
- Create: `bootleg/detect/__init__.py`
- Create: `bootleg/detect/features.py`
- Create: `bootleg/detect/geometry.py`
- Test: `tests/test_features.py`
- Test: `tests/test_geometry.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Player(cx, foot, h, v)`; `FeatureFrame(t_ms, n, near, far, hits, hit_reg)` with `.to_json_line()` / `FeatureFrame.from_json_line()`; `write_features(path, frames)` / `read_features(path) -> list[FeatureFrame]`; `Quad(points: tuple[tuple[float,float], ...])` with `.contains(x, y) -> bool` and `Quad.from_json(str)` / `.to_json()`

- [ ] **Step 1: Write the failing geometry test**

Create `tests/test_geometry.py`:

```python
import pytest
from bootleg.detect.geometry import Quad


@pytest.fixture
def unit_quad():
    return Quad(((0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)))


def test_contains_point_inside(unit_quad):
    assert unit_quad.contains(0.5, 0.5) is True


def test_contains_point_outside(unit_quad):
    assert unit_quad.contains(0.1, 0.5) is False
    assert unit_quad.contains(0.5, 0.95) is False


def test_contains_handles_trapezoid():
    # narrow at the top (far baseline), wide at the bottom (near baseline)
    q = Quad(((0.4, 0.3), (0.6, 0.3), (0.95, 1.0), (0.05, 1.0)))
    assert q.contains(0.5, 0.35) is True
    assert q.contains(0.1, 0.35) is False
    assert q.contains(0.1, 0.95) is True


def test_json_round_trip(unit_quad):
    assert Quad.from_json(unit_quad.to_json()) == unit_quad


def test_rejects_wrong_point_count():
    with pytest.raises(ValueError):
        Quad(((0.0, 0.0), (1.0, 1.0)))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_geometry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bootleg.detect'`

- [ ] **Step 3: Write `bootleg/detect/geometry.py`**

Create `bootleg/detect/__init__.py` (empty file).

```python
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Quad:
    """Four normalized (0-1) points describing the play region, in order."""

    points: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if len(self.points) != 4:
            raise ValueError(f"Quad needs exactly 4 points, got {len(self.points)}")

    def contains(self, x: float, y: float) -> bool:
        """Ray-casting point-in-polygon."""
        inside = False
        pts = self.points
        j = len(pts) - 1
        for i in range(len(pts)):
            xi, yi = pts[i]
            xj, yj = pts[j]
            if (yi > y) != (yj > y):
                x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
                if x < x_cross:
                    inside = not inside
            j = i
        return inside

    def to_json(self) -> str:
        return json.dumps([list(p) for p in self.points])

    @classmethod
    def from_json(cls, raw: str) -> "Quad":
        return cls(tuple((float(a), float(b)) for a, b in json.loads(raw)))
```

- [ ] **Step 4: Run geometry test to verify it passes**

Run: `pytest tests/test_geometry.py -v`
Expected: 5 passed

- [ ] **Step 5: Write the failing features test**

Create `tests/test_features.py`:

```python
from bootleg.detect.features import FeatureFrame, Player, read_features, write_features


def test_frame_json_round_trip():
    f = FeatureFrame(
        t_ms=41200, n=2,
        near=Player(cx=0.42, foot=0.88, h=0.31, v=1.8),
        far=Player(cx=0.55, foot=0.41, h=0.09, v=2.1),
        hits=2, hit_reg=0.81,
    )
    assert FeatureFrame.from_json_line(f.to_json_line()) == f


def test_frame_round_trip_with_missing_players():
    f = FeatureFrame(t_ms=0, n=0, near=None, far=None, hits=0, hit_reg=0.0)
    assert FeatureFrame.from_json_line(f.to_json_line()) == f


def test_write_then_read_file(tmp_path):
    frames = [
        FeatureFrame(t_ms=i * 200, n=1, near=Player(0.5, 0.9, 0.3, 0.4),
                     far=None, hits=0, hit_reg=0.0)
        for i in range(5)
    ]
    path = tmp_path / "features.jsonl"
    write_features(path, frames)
    assert read_features(path) == frames


def test_read_skips_blank_lines(tmp_path):
    path = tmp_path / "features.jsonl"
    path.write_text('{"t":0,"n":0,"hits":0,"hit_reg":0.0}\n\n')
    assert len(read_features(path)) == 1
```

- [ ] **Step 6: Run it to verify it fails**

Run: `pytest tests/test_features.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bootleg.detect.features'`

- [ ] **Step 7: Write `bootleg/detect/features.py`**

```python
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Player:
    cx: float     # bbox centre x, normalized
    foot: float   # bbox bottom y, normalized
    h: float      # bbox height, normalized — the distance proxy
    v: float      # speed in body-lengths per second


@dataclass(frozen=True)
class FeatureFrame:
    t_ms: int
    n: int                  # persons detected inside the play region
    near: Player | None
    far: Player | None
    hits: int               # audio impacts in the trailing 1 s
    hit_reg: float          # 0-1 regularity of the last 4 inter-hit intervals

    def to_json_line(self) -> str:
        d: dict = {"t": self.t_ms, "n": self.n, "hits": self.hits,
                   "hit_reg": round(self.hit_reg, 4)}
        if self.near is not None:
            d["near"] = {k: round(v, 4) for k, v in asdict(self.near).items()}
        if self.far is not None:
            d["far"] = {k: round(v, 4) for k, v in asdict(self.far).items()}
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_json_line(cls, line: str) -> "FeatureFrame":
        d = json.loads(line)
        return cls(
            t_ms=int(d["t"]),
            n=int(d["n"]),
            near=Player(**d["near"]) if "near" in d else None,
            far=Player(**d["far"]) if "far" in d else None,
            hits=int(d.get("hits", 0)),
            hit_reg=float(d.get("hit_reg", 0.0)),
        )


def write_features(path: Path, frames: Iterable[FeatureFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for frame in frames:
            fh.write(frame.to_json_line() + "\n")


def read_features(path: Path) -> list[FeatureFrame]:
    with path.open() as fh:
        return [FeatureFrame.from_json_line(ln) for ln in fh if ln.strip()]
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_features.py tests/test_geometry.py -v`
Expected: 9 passed

- [ ] **Step 9: Commit**

```bash
git add bootleg/detect tests/test_features.py tests/test_geometry.py
git commit -m "feat: add feature frame types and play-region geometry"
```

---

### Task 5: The segmenter

This is the highest-value unit in the project. It is a pure function, so it gets the densest test suite.

**Files:**
- Create: `bootleg/detect/segment.py`
- Test: `tests/test_segment.py`

**Interfaces:**
- Consumes: `FeatureFrame`, `Player` from Task 4
- Produces: `SegmentParams` (frozen dataclass, all fields defaulted); `Interval(start_ms, end_ms, confidence)`; `score_series(frames, params) -> list[float]`; `segment(frames, params) -> list[Interval]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_segment.py`:

```python
import pytest
from bootleg.detect.features import FeatureFrame, Player
from bootleg.detect.segment import Interval, SegmentParams, score_series, segment

SAMPLE_MS = 200  # 5 fps


def frames(spec: str, *, start_ms: int = 0) -> list[FeatureFrame]:
    """Build a feature stream from a compact string.

    'A' = both players active (rally-like)
    '.' = idle, nobody moving
    'O' = one player only, moving (ball retrieval)
    Each character is one 200 ms sample.
    """
    out = []
    for i, ch in enumerate(spec):
        t = start_ms + i * SAMPLE_MS
        if ch == "A":
            out.append(FeatureFrame(
                t, 2, Player(0.5, 0.9, 0.30, 2.0), Player(0.5, 0.4, 0.10, 2.0),
                hits=1, hit_reg=0.9))
        elif ch == "O":
            out.append(FeatureFrame(
                t, 1, Player(0.5, 0.9, 0.30, 2.0), None, hits=0, hit_reg=0.0))
        else:
            out.append(FeatureFrame(t, 0, None, None, hits=0, hit_reg=0.0))
    return out


@pytest.fixture
def params():
    return SegmentParams()


def test_empty_input_yields_no_intervals(params):
    assert segment([], params) == []


def test_all_idle_yields_no_intervals(params):
    assert segment(frames("." * 100), params) == []


def test_one_player_moving_is_not_a_rally(params):
    assert segment(frames("." * 20 + "O" * 40 + "." * 20), params) == []


def test_sustained_activity_yields_one_interval(params):
    # 40 samples of activity = 8 s
    result = segment(frames("." * 25 + "A" * 40 + "." * 25), params)
    assert len(result) == 1


def test_padding_is_applied(params):
    result = segment(frames("." * 25 + "A" * 40 + "." * 25), params)
    raw_start = 25 * SAMPLE_MS
    raw_end = 65 * SAMPLE_MS
    assert result[0].start_ms == raw_start - int(params.pad_start_s * 1000)
    assert result[0].end_ms == raw_end + int(params.pad_end_s * 1000)


def test_padding_clamps_at_zero(params):
    result = segment(frames("A" * 40 + "." * 25), params)
    assert result[0].start_ms == 0


def test_short_gap_is_closed(params):
    # 5 idle samples = 1.0 s < close_gap_s of 1.5 s
    result = segment(frames("." * 20 + "A" * 30 + "." * 5 + "A" * 30 + "." * 20), params)
    assert len(result) == 1


def test_long_gap_splits(params):
    # 20 idle samples = 4.0 s > close_gap_s
    result = segment(frames("." * 20 + "A" * 30 + "." * 20 + "A" * 30 + "." * 20), params)
    assert len(result) == 2


def test_ace_length_segment_survives(params):
    """A 2 s point is the canonical ace. The old 3 s floor deleted these."""
    result = segment(frames("." * 25 + "A" * 10 + "." * 25), params)
    assert len(result) == 1, "a 2 s rally must not be discarded"


def test_blip_below_min_duration_is_dropped(params):
    # 5 samples = 1.0 s < min_duration_s of 1.5 s
    assert segment(frames("." * 25 + "A" * 5 + "." * 25), params) == []


def test_confidence_is_between_zero_and_one(params):
    result = segment(frames("." * 25 + "A" * 40 + "." * 25), params)
    assert 0.0 <= result[0].confidence <= 1.0


def test_intervals_are_ordered_and_disjoint(params):
    spec = ("." * 20 + "A" * 30) * 3 + "." * 20
    result = segment(frames(spec), params)
    assert len(result) == 3
    for a, b in zip(result, result[1:]):
        assert a.end_ms < b.start_ms


def test_lower_threshold_finds_more(params):
    stream = frames("." * 20 + "A" * 20 + "." * 4 + "A" * 20 + "." * 20)
    strict = segment(stream, SegmentParams(threshold=0.95))
    loose = segment(stream, SegmentParams(threshold=0.15))
    assert len(loose) >= len(strict)


def test_audio_only_does_not_create_a_rally(params):
    """Hits with no player activity — an adjacent court — must not segment."""
    stream = [
        FeatureFrame(i * SAMPLE_MS, 0, None, None, hits=2, hit_reg=1.0)
        for i in range(60)
    ]
    assert segment(stream, params) == []


def test_score_series_length_matches_input(params):
    stream = frames("A" * 17)
    assert len(score_series(stream, params)) == 17
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_segment.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bootleg.detect.segment'`

- [ ] **Step 3: Write `bootleg/detect/segment.py`**

```python
import statistics
from dataclasses import dataclass

from bootleg.detect.features import FeatureFrame

MAX_SPEED = 4.0   # body-lengths/sec that counts as "fully moving"
MAX_HIT_RATE = 2.0  # impacts in the trailing second that counts as "full"


@dataclass(frozen=True)
class SegmentParams:
    w_both: float = 1.0
    w_speed: float = 0.9
    w_lateral: float = 0.3
    w_hits: float = 0.7
    w_regularity: float = 0.4
    w_outside: float = 1.2

    threshold: float = 0.45
    smooth_window_s: float = 1.0
    close_gap_s: float = 1.5
    min_duration_s: float = 1.5
    pad_start_s: float = 0.3
    pad_end_s: float = 0.5

    @property
    def weight_total(self) -> float:
        return self.w_both + self.w_speed + self.w_lateral + self.w_hits + self.w_regularity


@dataclass(frozen=True)
class Interval:
    start_ms: int
    end_ms: int
    confidence: float


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _raw_score(f: FeatureFrame, p: SegmentParams) -> float:
    both = 1.0 if (f.near is not None and f.far is not None) else 0.0

    if both:
        slower = min(f.near.v, f.far.v)
        speed = _clamp01(slower / MAX_SPEED)
        lateral = _clamp01(abs(f.near.cx - 0.5) * 2.0)
    else:
        speed = 0.0
        lateral = 0.0

    hits = _clamp01(f.hits / MAX_HIT_RATE)
    reg = _clamp01(f.hit_reg)

    # Audio alone must never carry a frame — an adjacent court would segment.
    if not both:
        hits = 0.0
        reg = 0.0

    outside = 1.0 if f.n > 0 and not both else 0.0

    score = (
        p.w_both * both
        + p.w_speed * speed
        + p.w_lateral * lateral
        + p.w_hits * hits
        + p.w_regularity * reg
        - p.w_outside * outside
    )
    return _clamp01(score / p.weight_total)


def _sample_interval_ms(frames: list[FeatureFrame]) -> int:
    if len(frames) < 2:
        return 200
    return max(1, frames[1].t_ms - frames[0].t_ms)


def score_series(frames: list[FeatureFrame], params: SegmentParams) -> list[float]:
    """Raw per-frame score, smoothed with a rolling median."""
    raw = [_raw_score(f, params) for f in frames]
    if not raw:
        return []

    step = _sample_interval_ms(frames)
    half = max(0, int((params.smooth_window_s * 1000) / step) // 2)
    if half == 0:
        return raw

    out = []
    for i in range(len(raw)):
        lo = max(0, i - half)
        hi = min(len(raw), i + half + 1)
        out.append(statistics.median(raw[lo:hi]))
    return out


def segment(frames: list[FeatureFrame], params: SegmentParams) -> list[Interval]:
    if not frames:
        return []

    scores = score_series(frames, params)
    step = _sample_interval_ms(frames)

    # 1. threshold to runs of indices
    runs: list[list[int]] = []
    current: list[int] = []
    for i, s in enumerate(scores):
        if s >= params.threshold:
            current.append(i)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    if not runs:
        return []

    # 2. close short gaps
    gap_samples = params.close_gap_s * 1000 / step
    merged: list[list[int]] = [runs[0]]
    for run in runs[1:]:
        if run[0] - merged[-1][-1] - 1 <= gap_samples:
            merged[-1] = merged[-1] + run
        else:
            merged.append(run)

    # 3. drop shorts, 4. pad, 5. score
    min_samples = params.min_duration_s * 1000 / step
    pad_start = int(params.pad_start_s * 1000)
    pad_end = int(params.pad_end_s * 1000)
    last_t = frames[-1].t_ms + step

    out: list[Interval] = []
    for run in merged:
        span = run[-1] - run[0] + 1
        if span < min_samples:
            continue
        start = frames[run[0]].t_ms
        end = frames[run[-1]].t_ms + step
        confidence = sum(scores[i] for i in run) / len(run)
        out.append(Interval(
            start_ms=max(0, start - pad_start),
            end_ms=min(last_t, end + pad_end),
            confidence=round(confidence, 4),
        ))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_segment.py -v`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add bootleg/detect/segment.py tests/test_segment.py
git commit -m "feat: add rally segmenter with ace-preserving 1.5s floor"
```

---

### Task 6: Audio impact detection

**Files:**
- Create: `bootleg/detect/audio.py`
- Test: `tests/test_audio.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Hit(t_ms: int, strength: float)`; `detect_hits(samples: np.ndarray, sr: int, *, highpass_hz: float = 800.0, k: float = 4.0, min_gap_ms: int = 120) -> list[Hit]`; `hits_to_grid(hits, duration_ms, step_ms=200) -> list[tuple[int, float]]` returning `(hits_in_trailing_1s, regularity)` per grid step; `extract_pcm(path: Path, sr: int = 22050) -> np.ndarray`

- [ ] **Step 1: Write the failing test**

Create `tests/test_audio.py`:

```python
import numpy as np
import pytest
from bootleg.detect.audio import Hit, detect_hits, hits_to_grid

SR = 22050


def click_track(intervals_s, *, duration_s=10.0, noise=0.02, amp=0.9, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.normal(0, noise, int(SR * duration_s)).astype(np.float32)
    t = 1.0
    for gap in intervals_s:
        i = int(t * SR)
        if i < len(x) - 200:
            # a short broadband transient, like ball on strings
            burst = rng.normal(0, 1, 200).astype(np.float32) * amp
            burst *= np.exp(-np.linspace(0, 6, 200))
            x[i:i + 200] += burst
        t += gap
    return x


def test_detects_evenly_spaced_hits():
    x = click_track([1.0] * 6)
    hits = detect_hits(x, SR)
    assert 5 <= len(hits) <= 8, f"expected ~6 hits, got {len(hits)}"


def test_ignores_pure_noise():
    rng = np.random.default_rng(1)
    x = rng.normal(0, 0.02, SR * 5).astype(np.float32)
    assert len(detect_hits(x, SR)) <= 1


def test_low_frequency_rumble_is_rejected():
    """Wind is low-frequency. The highpass must remove it."""
    t = np.arange(SR * 5) / SR
    wind = (0.8 * np.sin(2 * np.pi * 40 * t)).astype(np.float32)
    assert len(detect_hits(wind, SR)) == 0


def test_min_gap_suppresses_double_triggers():
    x = click_track([0.02, 0.02, 0.02], duration_s=5.0)
    hits = detect_hits(x, SR, min_gap_ms=200)
    assert len(hits) <= 2


def test_grid_counts_hits_in_trailing_second():
    hits = [Hit(1000, 1.0), Hit(1500, 1.0), Hit(2600, 1.0)]
    grid = hits_to_grid(hits, duration_ms=4000, step_ms=200)
    assert len(grid) == 20
    at_2000 = grid[10]           # t = 2000 ms; window covers 1000..2000
    assert at_2000[0] == 2
    at_3800 = grid[19]           # t = 3800 ms; window covers 2800..3800
    assert at_3800[0] == 0


def test_grid_regularity_is_high_for_metronomic_hits():
    hits = [Hit(1000 + i * 1000, 1.0) for i in range(5)]
    grid = hits_to_grid(hits, duration_ms=6000, step_ms=200)
    assert grid[-1][1] > 0.8


def test_grid_regularity_is_low_for_erratic_hits():
    hits = [Hit(1000, 1.0), Hit(1100, 1.0), Hit(3900, 1.0), Hit(4000, 1.0)]
    grid = hits_to_grid(hits, duration_ms=6000, step_ms=200)
    assert grid[-1][1] < 0.5


def test_grid_is_all_zero_without_hits():
    grid = hits_to_grid([], duration_ms=2000, step_ms=200)
    assert all(count == 0 and reg == 0.0 for count, reg in grid)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_audio.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bootleg.detect.audio'`

- [ ] **Step 3: Write `bootleg/detect/audio.py`**

```python
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt

ENVELOPE_MS = 10
ADAPTIVE_WINDOW_S = 2.0


@dataclass(frozen=True)
class Hit:
    t_ms: int
    strength: float


def extract_pcm(path: Path, sr: int = 22050) -> np.ndarray:
    """Decode a file's audio to mono float32 in [-1, 1]."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn",
         "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"audio extraction failed: {proc.stderr.decode().strip()}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def detect_hits(
    samples: np.ndarray,
    sr: int,
    *,
    highpass_hz: float = 800.0,
    k: float = 4.0,
    min_gap_ms: int = 120,
) -> list[Hit]:
    if samples.size < sr // 10:
        return []

    sos = butter(4, highpass_hz, btype="highpass", fs=sr, output="sos")
    filtered = sosfilt(sos, samples)

    hop = max(1, int(sr * ENVELOPE_MS / 1000))
    n = filtered.size // hop
    if n < 3:
        return []
    env = np.sqrt(np.mean(filtered[: n * hop].reshape(n, hop) ** 2, axis=1))

    win = max(3, int(ADAPTIVE_WINDOW_S * 1000 / ENVELOPE_MS))
    pad = win // 2
    padded = np.pad(env, pad, mode="edge")
    baseline = np.empty(n)
    spread = np.empty(n)
    for i in range(n):
        chunk = padded[i : i + win]
        med = np.median(chunk)
        baseline[i] = med
        spread[i] = np.median(np.abs(chunk - med))

    threshold = baseline + k * np.maximum(spread, 1e-6)

    hits: list[Hit] = []
    min_gap_frames = max(1, int(min_gap_ms / ENVELOPE_MS))
    last = -min_gap_frames
    for i in range(1, n - 1):
        if env[i] < threshold[i]:
            continue
        if env[i] < env[i - 1] or env[i] < env[i + 1]:
            continue
        if i - last < min_gap_frames:
            continue
        hits.append(Hit(t_ms=i * ENVELOPE_MS, strength=float(env[i] - baseline[i])))
        last = i
    return hits


def hits_to_grid(
    hits: list[Hit],
    duration_ms: int,
    step_ms: int = 200,
) -> list[tuple[int, float]]:
    """Per grid step: (impacts in the trailing 1 s, regularity of the last 4 gaps)."""
    steps = max(0, duration_ms // step_ms)
    times = [h.t_ms for h in hits]
    out: list[tuple[int, float]] = []

    for s in range(steps):
        t = s * step_ms
        window = [x for x in times if t - 1000 <= x <= t]
        count = len(window)

        recent = [x for x in times if x <= t][-5:]
        gaps = [b - a for a, b in zip(recent, recent[1:])]
        if len(gaps) >= 2:
            mean = statistics.fmean(gaps)
            reg = 0.0 if mean <= 0 else max(0.0, 1.0 - (statistics.pstdev(gaps) / mean))
        else:
            reg = 0.0

        out.append((count, round(reg, 4)))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_audio.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add bootleg/detect/audio.py tests/test_audio.py
git commit -m "feat: add audio impact detection with wind-rejecting highpass"
```

---

### Task 7: Vision feature extraction

The YOLO call is isolated behind a thin runner so the interesting logic stays pure and testable without a model.

**Files:**
- Create: `bootleg/detect/vision.py`
- Test: `tests/test_vision.py`

**Interfaces:**
- Consumes: `Quad` (Task 4), `Player`, `FeatureFrame` (Task 4)
- Produces: `Box(cx, cy, w, h)` normalized; `split_near_far(boxes, quad) -> tuple[Box | None, Box | None]`; `build_features(boxes_per_frame, quad, audio_grid, step_ms) -> list[FeatureFrame]`; `iter_person_boxes(proxy: Path, *, sample_fps: int = 5, imgsz: int = 960, model_name: str = "yolo11n.pt") -> Iterator[list[Box]]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_vision.py`:

```python
from bootleg.detect.geometry import Quad
from bootleg.detect.vision import Box, build_features, split_near_far

FULL = Quad(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))


def test_split_picks_largest_box_as_near():
    small = Box(cx=0.5, cy=0.40, w=0.03, h=0.09)
    big = Box(cx=0.4, cy=0.85, w=0.10, h=0.31)
    near, far = split_near_far([small, big], FULL)
    assert near == big
    assert far == small


def test_split_excludes_boxes_outside_the_quad():
    court = Quad(((0.3, 0.3), (0.7, 0.3), (0.7, 1.0), (0.3, 1.0)))
    inside = Box(cx=0.5, cy=0.85, w=0.10, h=0.30)
    adjacent_court = Box(cx=0.05, cy=0.85, w=0.10, h=0.30)
    near, far = split_near_far([inside, adjacent_court], court)
    assert near == inside
    assert far is None


def test_split_returns_none_for_empty_input():
    assert split_near_far([], FULL) == (None, None)


def test_split_with_one_box_assigns_near_only():
    near, far = split_near_far([Box(0.5, 0.85, 0.1, 0.3)], FULL)
    assert near is not None
    assert far is None


def test_build_features_computes_speed_in_body_lengths():
    # near player moves 0.15 in x over 200 ms with a body height of 0.30
    a = [Box(cx=0.40, cy=0.85, w=0.10, h=0.30)]
    b = [Box(cx=0.55, cy=0.85, w=0.10, h=0.30)]
    frames = build_features([a, b], FULL, audio_grid=[(0, 0.0), (0, 0.0)], step_ms=200)
    assert len(frames) == 2
    # 0.15 normalized units / 0.30 body height = 0.5 body-lengths over 0.2 s = 2.5 /s
    assert frames[1].near.v == 2.5


def test_build_features_first_frame_has_zero_speed():
    boxes = [[Box(0.5, 0.85, 0.1, 0.3)]] * 2
    frames = build_features(boxes, FULL, audio_grid=[(0, 0.0)] * 2, step_ms=200)
    assert frames[0].near.v == 0.0


def test_build_features_attaches_audio_grid():
    boxes = [[], []]
    frames = build_features(boxes, FULL, audio_grid=[(2, 0.8), (0, 0.0)], step_ms=200)
    assert frames[0].hits == 2
    assert frames[0].hit_reg == 0.8
    assert frames[1].hits == 0


def test_build_features_tolerates_short_audio_grid():
    boxes = [[], [], []]
    frames = build_features(boxes, FULL, audio_grid=[(1, 0.5)], step_ms=200)
    assert len(frames) == 3
    assert frames[2].hits == 0


def test_build_features_timestamps_step_correctly():
    boxes = [[], [], []]
    frames = build_features(boxes, FULL, audio_grid=[], step_ms=200)
    assert [f.t_ms for f in frames] == [0, 200, 400]


def test_build_features_counts_only_in_region():
    court = Quad(((0.3, 0.3), (0.7, 0.3), (0.7, 1.0), (0.3, 1.0)))
    boxes = [[Box(0.5, 0.85, 0.1, 0.3), Box(0.05, 0.85, 0.1, 0.3)]]
    frames = build_features(boxes, court, audio_grid=[], step_ms=200)
    assert frames[0].n == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_vision.py -v`
Expected: FAIL with `ImportError: cannot import name 'Box' from 'bootleg.detect.vision'`

- [ ] **Step 3: Write `bootleg/detect/vision.py`**

```python
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

import numpy as np

from bootleg.accel import detect_accel
from bootleg.detect.features import FeatureFrame, Player
from bootleg.detect.geometry import Quad


@dataclass(frozen=True)
class Box:
    """Person bounding box, normalized to the frame."""

    cx: float
    cy: float
    w: float
    h: float

    @property
    def foot(self) -> float:
        return self.cy + self.h / 2


def _in_region(box: Box, quad: Quad) -> bool:
    return quad.contains(box.cx, box.foot)


def split_near_far(
    boxes: Sequence[Box], quad: Quad
) -> tuple[Box | None, Box | None]:
    """Largest in-region box is the near player, next largest is the far one.

    Apparent height is the distance proxy: at a baseline camera the near
    player's box is 2-4x the far player's. This replaces a tracker, which is
    unreliable at 5 fps sampling.
    """
    inside = sorted((b for b in boxes if _in_region(b, quad)),
                    key=lambda b: b.h, reverse=True)
    near = inside[0] if inside else None
    far = inside[1] if len(inside) > 1 else None
    return near, far


def _to_player(box: Box | None, prev: Box | None, step_ms: int) -> Player | None:
    if box is None:
        return None
    if prev is None or box.h <= 0:
        v = 0.0
    else:
        dist = ((box.cx - prev.cx) ** 2 + (box.foot - prev.foot) ** 2) ** 0.5
        v = (dist / box.h) / (step_ms / 1000)
    return Player(cx=round(box.cx, 4), foot=round(box.foot, 4),
                  h=round(box.h, 4), v=round(v, 4))


def build_features(
    boxes_per_frame: Sequence[Sequence[Box]],
    quad: Quad,
    audio_grid: Sequence[tuple[int, float]],
    step_ms: int,
) -> list[FeatureFrame]:
    frames: list[FeatureFrame] = []
    prev_near: Box | None = None
    prev_far: Box | None = None

    for i, boxes in enumerate(boxes_per_frame):
        near, far = split_near_far(boxes, quad)
        hits, reg = audio_grid[i] if i < len(audio_grid) else (0, 0.0)
        frames.append(FeatureFrame(
            t_ms=i * step_ms,
            n=sum(1 for b in boxes if _in_region(b, quad)),
            near=_to_player(near, prev_near, step_ms),
            far=_to_player(far, prev_far, step_ms),
            hits=hits,
            hit_reg=reg,
        ))
        prev_near, prev_far = near, far

    return frames


def iter_person_boxes(
    proxy: Path,
    *,
    sample_fps: int = 5,
    imgsz: int = 960,
    model_name: str = "yolo11n.pt",
) -> Iterator[list[Box]]:
    """Decode the proxy at sample_fps and yield person boxes per frame.

    Not unit tested — inference is mocked everywhere else. Exercised only by
    the CLI against real footage.
    """
    from ultralytics import YOLO

    accel = detect_accel()
    width, height = 960, 540
    cmd = ["ffmpeg", "-v", "error"]
    if accel.hwaccel:
        cmd += ["-hwaccel", accel.hwaccel]
    cmd += ["-i", str(proxy), "-vf", f"fps={sample_fps},scale={width}:{height}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]

    model = YOLO(model_name)
    frame_bytes = width * height * 3

    with subprocess.Popen(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, bufsize=frame_bytes) as proc:
        while True:
            raw = proc.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
            result = model.predict(
                frame, imgsz=imgsz, classes=[0], device=accel.torch_device,
                verbose=False,
            )[0]

            boxes: list[Box] = []
            for x1, y1, x2, y2 in result.boxes.xyxy.tolist():
                boxes.append(Box(
                    cx=((x1 + x2) / 2) / width,
                    cy=((y1 + y2) / 2) / height,
                    w=(x2 - x1) / width,
                    h=(y2 - y1) / height,
                ))
            yield boxes

        stderr = proc.stderr.read().decode()
        if proc.wait() != 0:
            raise RuntimeError(f"frame decode failed: {stderr.strip()}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vision.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add bootleg/detect/vision.py tests/test_vision.py
git commit -m "feat: add vision feature extraction with size-based near/far split"
```

---

### Task 8: Transcode wrappers

**Files:**
- Create: `bootleg/media/transcode.py`
- Test: `tests/test_transcode.py`

**Interfaces:**
- Consumes: `Accel`, `detect_accel` (Task 3), `probe` (Task 3)
- Produces: `make_proxy(src, dst, accel=None) -> None`; `make_thumbs(src, dst, every_s=10, cols=10, tile_w=160) -> None`; exception `TranscodeError`; `run_ffmpeg(args: list[str]) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_transcode.py`:

```python
import subprocess
import pytest
from bootleg.media.probe import probe
from bootleg.media.transcode import TranscodeError, make_proxy, make_thumbs, run_ffmpeg


@pytest.fixture
def big_video(tmp_path):
    """3 second 1920x1080 clip, so the proxy has something to scale down."""
    out = tmp_path / "big.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1920x1080:rate=30:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_make_proxy_outputs_1080p_h264(big_video, tmp_path):
    dst = tmp_path / "proxy.mp4"
    make_proxy(big_video, dst)
    assert dst.exists()
    info = probe(dst)
    assert info.height == 1080
    assert 2900 <= info.duration_ms <= 3100


def test_make_proxy_uses_short_gop(big_video, tmp_path):
    """Keyframe every ~1s so scrubbing snaps. At 30fps that is >= 3 in 3s."""
    dst = tmp_path / "proxy.mp4"
    make_proxy(big_video, dst)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "frame=key_frame", "-of", "csv=p=0", str(dst)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert out.count("1") >= 3


def test_make_thumbs_writes_a_sprite_sheet(big_video, tmp_path):
    dst = tmp_path / "thumbs.jpg"
    make_thumbs(big_video, dst, every_s=1)
    assert dst.exists()
    assert dst.stat().st_size > 0


def test_run_ffmpeg_raises_with_stderr_on_failure(tmp_path):
    with pytest.raises(TranscodeError) as exc:
        run_ffmpeg(["-i", str(tmp_path / "nope.mp4"), str(tmp_path / "out.mp4")])
    assert "nope.mp4" in str(exc.value)


def test_make_proxy_creates_parent_directories(big_video, tmp_path):
    dst = tmp_path / "a" / "b" / "proxy.mp4"
    make_proxy(big_video, dst)
    assert dst.exists()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_transcode.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bootleg.media.transcode'`

- [ ] **Step 3: Write `bootleg/media/transcode.py`**

```python
import subprocess
from pathlib import Path

from bootleg.accel import Accel, detect_accel


class TranscodeError(Exception):
    """An ffmpeg invocation failed."""


def run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *args],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise TranscodeError(
            f"ffmpeg failed (exit {proc.returncode})\n"
            f"args: {' '.join(args)}\n{proc.stderr.strip()}"
        )


def make_proxy(src: Path, dst: Path, accel: Accel | None = None) -> None:
    """1080p H.264 with a 1-second GOP. H.264 because browser HEVC is a coin flip."""
    accel = accel or detect_accel()
    dst.parent.mkdir(parents=True, exist_ok=True)

    args: list[str] = []
    if accel.hwaccel:
        args += ["-hwaccel", accel.hwaccel]
    args += [
        "-i", str(src),
        "-vf", "scale=-2:1080:flags=bicubic",
        "-c:v", accel.h264_encoder,
        "-g", "30",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
    ]
    if accel.h264_encoder == "libx264":
        args += ["-crf", "21", "-preset", "veryfast"]
    else:
        args += ["-b:v", "8M"]
    args.append(str(dst))

    run_ffmpeg(args)


def make_thumbs(
    src: Path, dst: Path, every_s: int = 10, cols: int = 10, tile_w: int = 160
) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-i", str(src),
        "-vf", f"fps=1/{every_s},scale={tile_w}:-2,tile={cols}x{cols}",
        "-frames:v", "1",
        "-q:v", "4",
        str(dst),
    ])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_transcode.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add bootleg/media/transcode.py tests/test_transcode.py
git commit -m "feat: add proxy and sprite-sheet transcode wrappers"
```

---

### Task 9: Session, source and rally repositories

**Files:**
- Create: `bootleg/db/sessions.py`
- Create: `bootleg/db/rallies.py`
- Test: extend `tests/test_db.py`

**Interfaces:**
- Consumes: `connect`, `migrate` (Task 2), `Interval` (Task 5)
- Produces:
  - `create_session(conn, session_id, title, played_on) -> str`
  - `find_or_create_session_for_date(conn, played_on) -> str`
  - `add_source(conn, session_id, recorded_at, duration_ms, width, height, fps, original_name) -> tuple[str, int]` returning `(source_id, idx)`; sets `offset_ms` to the cumulative duration of existing sources
  - `get_session(conn, session_id) -> sqlite3.Row | None`
  - `list_sessions(conn) -> list[sqlite3.Row]`
  - `list_sources(conn, session_id) -> list[sqlite3.Row]`
  - `get_source(conn, source_id) -> sqlite3.Row | None`
  - `set_source_status(conn, source_id, status) -> None`
  - `set_session_status(conn, session_id, status) -> None`
  - `replace_rallies(conn, session_id, source_id, intervals: list[Interval]) -> int`
  - `list_rallies(conn, session_id) -> list[sqlite3.Row]`
  - `set_star(conn, rally_id, starred: bool) -> None`
  - `set_rejected(conn, rally_id, rejected: bool) -> None`
  - `set_bounds(conn, rally_id, start_ms, end_ms) -> None`
  - `mark_reviewed(conn, rally_id) -> None` — unused in this plan; Plan 2's queue mode calls it

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db.py`:

```python
from bootleg.db.rallies import (
    list_rallies, replace_rallies, set_bounds, set_rejected, set_star,
)
from bootleg.db.sessions import (
    add_source, find_or_create_session_for_date, list_sources,
)
from bootleg.detect.segment import Interval


@pytest.fixture
def conn(library):
    c = connect(library.db_path)
    migrate(c)
    return c


def _add(c, session_id, duration_ms):
    return add_source(c, session_id, recorded_at="2026-08-19T10:00:00Z",
                      duration_ms=duration_ms, width=3840, height=2160,
                      fps=30.0, original_name="IMG_0001.MOV")


def test_find_or_create_is_stable_for_the_same_date(conn):
    a = find_or_create_session_for_date(conn, "2026-08-19")
    b = find_or_create_session_for_date(conn, "2026-08-19")
    assert a == b


def test_sources_get_sequential_idx_and_cumulative_offset(conn):
    s = find_or_create_session_for_date(conn, "2026-08-19")
    _, idx1 = _add(conn, s, 60_000)
    _, idx2 = _add(conn, s, 30_000)
    assert (idx1, idx2) == (1, 2)
    rows = list_sources(conn, s)
    assert [r["offset_ms"] for r in rows] == [0, 60_000]


def test_replace_rallies_writes_det_columns(conn):
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src, _ = _add(conn, s, 60_000)
    replace_rallies(conn, s, src, [Interval(1000, 4000, 0.8)])
    r = list_rallies(conn, s)[0]
    assert (r["start_ms"], r["end_ms"]) == (1000, 4000)
    assert (r["det_start_ms"], r["det_end_ms"]) == (1000, 4000)


def test_set_bounds_leaves_det_columns_untouched(conn):
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src, _ = _add(conn, s, 60_000)
    replace_rallies(conn, s, src, [Interval(1000, 4000, 0.8)])
    rally_id = list_rallies(conn, s)[0]["id"]
    set_bounds(conn, rally_id, 1200, 3800)
    r = list_rallies(conn, s)[0]
    assert (r["start_ms"], r["end_ms"]) == (1200, 3800)
    assert (r["det_start_ms"], r["det_end_ms"]) == (1000, 4000)


def test_replace_rallies_preserves_stars_by_overlap(conn):
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src, _ = _add(conn, s, 60_000)
    replace_rallies(conn, s, src, [Interval(1000, 5000, 0.8)])
    set_star(conn, list_rallies(conn, s)[0]["id"], True)

    # re-segment produces a slightly different but heavily overlapping segment
    replace_rallies(conn, s, src, [Interval(1200, 5200, 0.7)])
    rows = list_rallies(conn, s)
    assert len(rows) == 1
    assert rows[0]["starred"] == 1


def test_replace_rallies_drops_stars_when_overlap_is_small(conn):
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src, _ = _add(conn, s, 60_000)
    replace_rallies(conn, s, src, [Interval(1000, 5000, 0.8)])
    set_star(conn, list_rallies(conn, s)[0]["id"], True)

    replace_rallies(conn, s, src, [Interval(30_000, 34_000, 0.7)])
    assert list_rallies(conn, s)[0]["starred"] == 0


def test_rallies_are_renumbered_across_sources(conn):
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src1, _ = _add(conn, s, 60_000)
    src2, _ = _add(conn, s, 60_000)
    replace_rallies(conn, s, src1, [Interval(1000, 4000, 0.8)])
    replace_rallies(conn, s, src2, [Interval(2000, 5000, 0.8)])
    assert [r["idx"] for r in list_rallies(conn, s)] == [1, 2]


def test_set_rejected_hides_nothing_but_flags_the_row(conn):
    s = find_or_create_session_for_date(conn, "2026-08-19")
    src, _ = _add(conn, s, 60_000)
    replace_rallies(conn, s, src, [Interval(1000, 4000, 0.8)])
    rally_id = list_rallies(conn, s)[0]["id"]
    set_rejected(conn, rally_id, True)
    assert list_rallies(conn, s)[0]["rejected"] == 1
```

Add `import pytest` to the top of `tests/test_db.py` if it is not already there.

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bootleg.db.sessions'`

- [ ] **Step 3: Write `bootleg/db/sessions.py`**

```python
import sqlite3
import uuid
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_session(conn: sqlite3.Connection, session_id: str,
                   title: str, played_on: str) -> str:
    conn.execute(
        "INSERT INTO sessions (id,title,played_on,status,created_at)"
        " VALUES (?,?,?,'ingesting',?)",
        (session_id, title, played_on, _now()),
    )
    conn.commit()
    return session_id


def find_or_create_session_for_date(conn: sqlite3.Connection, played_on: str) -> str:
    row = conn.execute(
        "SELECT id FROM sessions WHERE played_on = ? ORDER BY id LIMIT 1",
        (played_on,),
    ).fetchone()
    if row:
        return row["id"]
    return create_session(conn, played_on, played_on, played_on)


def add_source(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    recorded_at: str,
    duration_ms: int,
    width: int,
    height: int,
    fps: float,
    original_name: str | None,
) -> tuple[str, int]:
    row = conn.execute(
        "SELECT COALESCE(MAX(idx),0) AS max_idx,"
        " COALESCE(SUM(duration_ms),0) AS total FROM sources WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    idx = row["max_idx"] + 1
    offset_ms = row["total"]
    source_id = uuid.uuid4().hex

    conn.execute(
        "INSERT INTO sources (id,session_id,idx,recorded_at,offset_ms,duration_ms,"
        "width,height,fps,original_name,status) VALUES (?,?,?,?,?,?,?,?,?,?,'ingesting')",
        (source_id, session_id, idx, recorded_at, offset_ms, duration_ms,
         width, height, fps, original_name),
    )
    conn.commit()
    return source_id, idx


def get_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()


def list_sessions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sessions ORDER BY played_on DESC, id DESC"
    ).fetchall()


def list_sources(conn: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sources WHERE session_id = ? ORDER BY idx", (session_id,)
    ).fetchall()


def get_source(conn: sqlite3.Connection, source_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()


def set_source_status(conn: sqlite3.Connection, source_id: str, status: str) -> None:
    conn.execute("UPDATE sources SET status = ? WHERE id = ?", (status, source_id))
    conn.commit()


def set_session_status(conn: sqlite3.Connection, session_id: str, status: str) -> None:
    conn.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))
    conn.commit()
```

- [ ] **Step 4: Write `bootleg/db/rallies.py`**

```python
import sqlite3
import uuid
from datetime import datetime, timezone

from bootleg.detect.segment import Interval

STAR_OVERLAP_MIN = 0.5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _overlap_fraction(a_start: int, a_end: int, b_start: int, b_end: int) -> float:
    overlap = min(a_end, b_end) - max(a_start, b_start)
    if overlap <= 0:
        return 0.0
    return overlap / max(1, min(a_end - a_start, b_end - b_start))


def replace_rallies(
    conn: sqlite3.Connection,
    session_id: str,
    source_id: str,
    intervals: list[Interval],
) -> int:
    """Rewrite one source's rallies, carrying stars across by overlap.

    Manual boundary edits are intentionally not preserved — the caller
    confirms that loss before calling.
    """
    old = conn.execute(
        "SELECT start_ms, end_ms FROM rallies WHERE source_id = ? AND starred = 1",
        (source_id,),
    ).fetchall()

    conn.execute("DELETE FROM rallies WHERE source_id = ?", (source_id,))

    for iv in intervals:
        starred = any(
            _overlap_fraction(iv.start_ms, iv.end_ms, r["start_ms"], r["end_ms"])
            >= STAR_OVERLAP_MIN
            for r in old
        )
        conn.execute(
            "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
            "det_start_ms,det_end_ms,confidence,starred) VALUES (?,?,?,0,?,?,?,?,?,?)",
            (uuid.uuid4().hex, session_id, source_id, iv.start_ms, iv.end_ms,
             iv.start_ms, iv.end_ms, iv.confidence, int(starred)),
        )

    _renumber(conn, session_id)
    conn.commit()
    return len(intervals)


def _renumber(conn: sqlite3.Connection, session_id: str) -> None:
    rows = conn.execute(
        "SELECT r.id FROM rallies r JOIN sources s ON s.id = r.source_id"
        " WHERE r.session_id = ? ORDER BY s.idx, r.start_ms",
        (session_id,),
    ).fetchall()
    for i, row in enumerate(rows, start=1):
        conn.execute("UPDATE rallies SET idx = ? WHERE id = ?", (i, row["id"]))


def list_rallies(conn: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM rallies WHERE session_id = ? ORDER BY idx", (session_id,)
    ).fetchall()


def set_star(conn: sqlite3.Connection, rally_id: str, starred: bool) -> None:
    conn.execute(
        "UPDATE rallies SET starred = ?, reviewed_at = COALESCE(reviewed_at, ?)"
        " WHERE id = ?",
        (int(starred), _now(), rally_id),
    )
    conn.commit()


def set_rejected(conn: sqlite3.Connection, rally_id: str, rejected: bool) -> None:
    conn.execute(
        "UPDATE rallies SET rejected = ?, reviewed_at = COALESCE(reviewed_at, ?)"
        " WHERE id = ?",
        (int(rejected), _now(), rally_id),
    )
    conn.commit()


def mark_reviewed(conn: sqlite3.Connection, rally_id: str) -> None:
    conn.execute(
        "UPDATE rallies SET reviewed_at = COALESCE(reviewed_at, ?) WHERE id = ?",
        (_now(), rally_id),
    )
    conn.commit()


def set_bounds(conn: sqlite3.Connection, rally_id: str,
               start_ms: int, end_ms: int) -> None:
    """Update working bounds only. det_* columns are immutable training data."""
    conn.execute(
        "UPDATE rallies SET start_ms = ?, end_ms = ? WHERE id = ?",
        (start_ms, end_ms, rally_id),
    )
    conn.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: 12 passed

- [ ] **Step 6: Commit**

```bash
git add bootleg/db/sessions.py bootleg/db/rallies.py tests/test_db.py
git commit -m "feat: add session, source and rally repositories with star carry-over"
```

---

### Task 10: Job queue and worker

**Files:**
- Create: `bootleg/db/jobs.py`
- Create: `bootleg/jobs/__init__.py`
- Create: `bootleg/jobs/worker.py`
- Test: `tests/test_jobs.py`

**Interfaces:**
- Consumes: `connect`, `migrate` (Task 2)
- Produces:
  - `enqueue(conn, job_type: str, payload: dict) -> str`
  - `claim(conn) -> sqlite3.Row | None` — atomically flips the oldest `queued` row to `running`
  - `heartbeat(conn, job_id) -> None`
  - `set_progress(conn, job_id, progress: float) -> None`
  - `finish(conn, job_id, error: str | None = None) -> None`
  - `reclaim_stale(conn, older_than_s: int = 120) -> int`
  - `Worker(library, handlers: dict[str, Callable[[Library, dict], None]])` with `.run_once() -> bool` and `.start()` / `.stop()`

- [ ] **Step 1: Write the failing test**

Create `tests/test_jobs.py`:

```python
import json
import pytest
from datetime import datetime, timedelta, timezone

from bootleg.db.jobs import (
    claim, enqueue, finish, heartbeat, reclaim_stale, set_progress,
)
from bootleg.db.schema import connect, migrate
from bootleg.jobs.worker import Worker


@pytest.fixture
def conn(library):
    c = connect(library.db_path)
    migrate(c)
    return c


def test_enqueue_then_claim_returns_the_job(conn):
    job_id = enqueue(conn, "ingest", {"path": "/tmp/a.mov"})
    job = claim(conn)
    assert job["id"] == job_id
    assert job["status"] == "running"
    assert json.loads(job["payload"])["path"] == "/tmp/a.mov"


def test_claim_returns_none_when_queue_empty(conn):
    assert claim(conn) is None


def test_claim_is_fifo(conn):
    first = enqueue(conn, "ingest", {"n": 1})
    enqueue(conn, "ingest", {"n": 2})
    assert claim(conn)["id"] == first


def test_claim_does_not_return_running_jobs(conn):
    enqueue(conn, "ingest", {})
    claim(conn)
    assert claim(conn) is None


def test_finish_marks_done(conn):
    job_id = enqueue(conn, "ingest", {})
    claim(conn)
    finish(conn, job_id)
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "done"
    assert row["finished_at"] is not None


def test_finish_with_error_marks_failed_and_stores_message(conn):
    job_id = enqueue(conn, "ingest", {})
    claim(conn)
    finish(conn, job_id, error="ffmpeg exploded")
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert "exploded" in row["error"]


def test_reclaim_stale_requeues_abandoned_jobs(conn):
    job_id = enqueue(conn, "ingest", {})
    claim(conn)
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    conn.execute("UPDATE jobs SET heartbeat_at=? WHERE id=?", (old, job_id))
    conn.commit()

    assert reclaim_stale(conn, older_than_s=120) == 1
    assert conn.execute(
        "SELECT status FROM jobs WHERE id=?", (job_id,)
    ).fetchone()["status"] == "queued"


def test_reclaim_leaves_fresh_jobs_alone(conn):
    enqueue(conn, "ingest", {})
    claim(conn)
    assert reclaim_stale(conn, older_than_s=120) == 0


def test_set_progress_updates_the_row(conn):
    job_id = enqueue(conn, "ingest", {})
    claim(conn)
    set_progress(conn, job_id, 0.42)
    assert conn.execute(
        "SELECT progress FROM jobs WHERE id=?", (job_id,)
    ).fetchone()["progress"] == pytest.approx(0.42)


def test_worker_run_once_dispatches_to_the_handler(library, conn):
    seen = []
    worker = Worker(library, handlers={"ingest": lambda lib, payload: seen.append(payload)})
    enqueue(conn, "ingest", {"path": "x.mov"})

    assert worker.run_once() is True
    assert seen == [{"path": "x.mov"}]


def test_worker_run_once_returns_false_when_idle(library):
    worker = Worker(library, handlers={})
    assert worker.run_once() is False


def test_worker_records_handler_exceptions_as_failures(library, conn):
    def boom(lib, payload):
        raise ValueError("nope")

    worker = Worker(library, handlers={"ingest": boom})
    job_id = enqueue(conn, "ingest", {})
    worker.run_once()

    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert "nope" in row["error"]


def test_worker_fails_unknown_job_types(library, conn):
    worker = Worker(library, handlers={})
    job_id = enqueue(conn, "mystery", {})
    worker.run_once()
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert "mystery" in row["error"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_jobs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bootleg.db.jobs'`

- [ ] **Step 3: Write `bootleg/db/jobs.py`**

```python
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue(conn: sqlite3.Connection, job_type: str, payload: dict) -> str:
    job_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO jobs (id,type,payload,status,created_at)"
        " VALUES (?,?,?,'queued',?)",
        (job_id, job_type, json.dumps(payload), _now()),
    )
    conn.commit()
    return job_id


def claim(conn: sqlite3.Connection) -> sqlite3.Row | None:
    with conn:  # implicit transaction; SQLite serializes writers
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at, id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE jobs SET status='running', heartbeat_at=? WHERE id=?",
            (_now(), row["id"]),
        )
    return conn.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()


def heartbeat(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute("UPDATE jobs SET heartbeat_at=? WHERE id=?", (_now(), job_id))
    conn.commit()


def set_progress(conn: sqlite3.Connection, job_id: str, progress: float) -> None:
    conn.execute(
        "UPDATE jobs SET progress=?, heartbeat_at=? WHERE id=?",
        (progress, _now(), job_id),
    )
    conn.commit()


def finish(conn: sqlite3.Connection, job_id: str, error: str | None = None) -> None:
    conn.execute(
        "UPDATE jobs SET status=?, error=?, finished_at=? WHERE id=?",
        ("failed" if error else "done", error, _now(), job_id),
    )
    conn.commit()


def reclaim_stale(conn: sqlite3.Connection, older_than_s: int = 120) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_s)).isoformat()
    cur = conn.execute(
        "UPDATE jobs SET status='queued', heartbeat_at=NULL"
        " WHERE status='running' AND (heartbeat_at IS NULL OR heartbeat_at < ?)",
        (cutoff,),
    )
    conn.commit()
    return cur.rowcount
```

- [ ] **Step 4: Write `bootleg/jobs/worker.py`**

Create `bootleg/jobs/__init__.py` (empty file).

```python
import json
import logging
import threading
import traceback
from typing import Callable

from bootleg.config import Library
from bootleg.db import jobs as jobq
from bootleg.db.schema import connect, migrate

log = logging.getLogger(__name__)

Handler = Callable[[Library, dict], None]
POLL_SECONDS = 1.0


class Worker:
    """Claims one job at a time. All handlers are idempotent, so retry is safe."""

    def __init__(self, library: Library, handlers: dict[str, Handler]):
        self.library = library
        self.handlers = handlers
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.conn = connect(library.db_path)
        migrate(self.conn)

    def run_once(self) -> bool:
        job = jobq.claim(self.conn)
        if job is None:
            return False

        handler = self.handlers.get(job["type"])
        if handler is None:
            jobq.finish(self.conn, job["id"], error=f"Unknown job type: {job['type']}")
            return True

        try:
            handler(self.library, json.loads(job["payload"]))
        except Exception:
            jobq.finish(self.conn, job["id"], error=traceback.format_exc(limit=6))
            log.exception("job %s failed", job["id"])
        else:
            jobq.finish(self.conn, job["id"])
        return True

    def _loop(self) -> None:
        jobq.reclaim_stale(self.conn)
        while not self._stop.is_set():
            try:
                if not self.run_once():
                    self._stop.wait(POLL_SECONDS)
            except Exception:
                log.exception("worker loop error")
                self._stop.wait(POLL_SECONDS)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="worker")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_jobs.py -v`
Expected: 13 passed

- [ ] **Step 6: Commit**

```bash
git add bootleg/db/jobs.py bootleg/jobs tests/test_jobs.py
git commit -m "feat: add sqlite job queue and worker thread"
```

---

### Task 11: Ingest and detect handlers

**Files:**
- Create: `bootleg/jobs/handlers.py`
- Test: `tests/test_handlers.py`

**Interfaces:**
- Consumes: everything from Tasks 1-10
- Produces:
  - `handle_ingest(library, payload) -> None` — payload `{"path": str}`; probes, assigns to a date session, creates the source row, moves the file, writes proxy + thumbs, enqueues `detect`
  - `handle_detect(library, payload) -> None` — payload `{"source_id": str, "quad": str | None}`; runs audio + vision, writes `features.jsonl`, segments, replaces rallies
  - `DEFAULT_QUAD: Quad` — full frame, used when a source has no court preset
  - `HANDLERS: dict[str, Handler]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_handlers.py`:

```python
import json
import subprocess
import pytest

from bootleg.db.rallies import list_rallies
from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import list_sessions, list_sources
from bootleg.detect.features import FeatureFrame, Player, write_features
from bootleg.jobs.handlers import handle_detect, handle_ingest


@pytest.fixture
def conn(library):
    c = connect(library.db_path)
    migrate(c)
    return c


@pytest.fixture
def dropped_video(library):
    out = library.inbox / "IMG_0001.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=640x360:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_ingest_creates_session_source_and_proxy(library, conn, dropped_video):
    handle_ingest(library, {"path": str(dropped_video)})

    sessions = list_sessions(conn)
    assert len(sessions) == 1

    sources = list_sources(conn, sessions[0]["id"])
    assert len(sources) == 1
    assert sources[0]["original_name"] == "IMG_0001.mp4"

    src_dir = library.source_dir(sessions[0]["id"], 1)
    assert (src_dir / "proxy.mp4").exists()
    assert (src_dir / "thumbs.jpg").exists()
    assert (src_dir / "original.mp4").exists()


def test_ingest_removes_the_file_from_the_inbox(library, conn, dropped_video):
    handle_ingest(library, {"path": str(dropped_video)})
    assert not dropped_video.exists()


def test_ingest_enqueues_a_detect_job(library, conn, dropped_video):
    handle_ingest(library, {"path": str(dropped_video)})
    row = conn.execute("SELECT * FROM jobs WHERE type='detect'").fetchone()
    assert row is not None
    assert "source_id" in json.loads(row["payload"])


def test_second_file_same_day_joins_the_same_session(library, conn, dropped_video):
    handle_ingest(library, {"path": str(dropped_video)})
    second = library.inbox / "IMG_0002.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=640x360:rate=30:duration=2",
         "-c:v", "libx264", str(second)],
        check=True, capture_output=True,
    )
    handle_ingest(library, {"path": str(second)})

    sessions = list_sessions(conn)
    assert len(sessions) == 1
    assert len(list_sources(conn, sessions[0]["id"])) == 2


def test_detect_segments_from_cached_features(library, conn, dropped_video, monkeypatch):
    handle_ingest(library, {"path": str(dropped_video)})
    session_id = list_sessions(conn)[0]["id"]
    source = list_sources(conn, session_id)[0]

    # Pre-write features so no YOLO is needed: 8 s of two-player activity.
    frames = [
        FeatureFrame(i * 200, 2,
                     Player(0.5, 0.9, 0.30, 2.5), Player(0.5, 0.4, 0.10, 2.5),
                     hits=1, hit_reg=0.9)
        for i in range(40)
    ]
    write_features(library.source_dir(session_id, 1) / "features.jsonl", frames)

    handle_detect(library, {"source_id": source["id"], "reuse_features": True})

    rallies = list_rallies(conn, session_id)
    assert len(rallies) == 1
    assert rallies[0]["det_start_ms"] == rallies[0]["start_ms"]


def test_detect_is_idempotent(library, conn, dropped_video):
    handle_ingest(library, {"path": str(dropped_video)})
    session_id = list_sessions(conn)[0]["id"]
    source = list_sources(conn, session_id)[0]

    frames = [
        FeatureFrame(i * 200, 2,
                     Player(0.5, 0.9, 0.30, 2.5), Player(0.5, 0.4, 0.10, 2.5),
                     hits=1, hit_reg=0.9)
        for i in range(40)
    ]
    write_features(library.source_dir(session_id, 1) / "features.jsonl", frames)

    handle_detect(library, {"source_id": source["id"], "reuse_features": True})
    handle_detect(library, {"source_id": source["id"], "reuse_features": True})
    assert len(list_rallies(conn, session_id)) == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_handlers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bootleg.jobs.handlers'`

- [ ] **Step 3: Write `bootleg/jobs/handlers.py`**

```python
import logging
import shutil
from pathlib import Path

from bootleg.config import Library
from bootleg.db import jobs as jobq
from bootleg.db.rallies import replace_rallies
from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import (
    add_source, find_or_create_session_for_date, get_source,
    set_session_status, set_source_status,
)
from bootleg.detect.audio import detect_hits, extract_pcm, hits_to_grid
from bootleg.detect.features import read_features, write_features
from bootleg.detect.geometry import Quad
from bootleg.detect.segment import SegmentParams, segment
from bootleg.detect.vision import build_features, iter_person_boxes
from bootleg.media.probe import probe
from bootleg.media.transcode import make_proxy, make_thumbs

log = logging.getLogger(__name__)

SAMPLE_FPS = 5
STEP_MS = 1000 // SAMPLE_FPS
AUDIO_SR = 22050

# Whole frame. Replaced by a court preset once one exists for the source.
DEFAULT_QUAD = Quad(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))


def _open(library: Library):
    conn = connect(library.db_path)
    migrate(conn)
    return conn


def _played_on(recorded_at: str | None, fallback: Path) -> str:
    if recorded_at:
        return recorded_at[:10]
    from datetime import datetime, timezone
    ts = datetime.fromtimestamp(fallback.stat().st_mtime, tz=timezone.utc)
    return ts.date().isoformat()


def handle_ingest(library: Library, payload: dict) -> None:
    src = Path(payload["path"])
    conn = _open(library)

    info = probe(src)
    # Proxy plus sprite sheet run roughly 1.5x the source in the worst case.
    library.require_free(int(src.stat().st_size * 1.5))
    played_on = _played_on(info.recorded_at, src)
    session_id = find_or_create_session_for_date(conn, played_on)

    source_id, idx = add_source(
        conn, session_id,
        recorded_at=info.recorded_at or played_on,
        duration_ms=info.duration_ms,
        width=info.width, height=info.height, fps=info.fps,
        original_name=src.name,
    )

    dest_dir = library.source_dir(session_id, idx)
    dest_dir.mkdir(parents=True, exist_ok=True)
    original = dest_dir / f"original{src.suffix.lower()}"
    shutil.move(str(src), original)

    make_proxy(original, dest_dir / "proxy.mp4")
    make_thumbs(dest_dir / "proxy.mp4", dest_dir / "thumbs.jpg")

    set_source_status(conn, source_id, "ingested")
    set_session_status(conn, session_id, "detecting")
    jobq.enqueue(conn, "detect", {"source_id": source_id})


def _quad_for(conn, source) -> Quad:
    if source["court_preset_id"]:
        row = conn.execute(
            "SELECT quad FROM court_presets WHERE id = ?",
            (source["court_preset_id"],),
        ).fetchone()
        if row:
            return Quad.from_json(row["quad"])
    return DEFAULT_QUAD


def handle_detect(library: Library, payload: dict) -> None:
    conn = _open(library)
    source = get_source(conn, payload["source_id"])
    if source is None:
        raise ValueError(f"No such source: {payload['source_id']}")

    src_dir = library.source_dir(source["session_id"], source["idx"])
    proxy = src_dir / "proxy.mp4"
    features_path = src_dir / "features.jsonl"

    if payload.get("reuse_features") and features_path.exists():
        frames = read_features(features_path)
    else:
        set_source_status(conn, source["id"], "detecting")
        grid = _audio_grid(proxy, source["duration_ms"])
        quad = _quad_for(conn, source)
        boxes = list(iter_person_boxes(proxy, sample_fps=SAMPLE_FPS))
        frames = build_features(boxes, quad, grid, STEP_MS)
        write_features(features_path, frames)

    intervals = segment(frames, SegmentParams())
    replace_rallies(conn, source["session_id"], source["id"], intervals)

    set_source_status(conn, source["id"], "ready")
    if all(r["status"] == "ready" for r in conn.execute(
            "SELECT status FROM sources WHERE session_id = ?",
            (source["session_id"],))):
        set_session_status(conn, source["session_id"], "ready")


def _audio_grid(proxy: Path, duration_ms: int) -> list[tuple[int, float]]:
    try:
        pcm = extract_pcm(proxy, sr=AUDIO_SR)
    except Exception:
        log.warning("no usable audio in %s; continuing without it", proxy)
        return []
    return hits_to_grid(detect_hits(pcm, AUDIO_SR), duration_ms, step_ms=STEP_MS)


HANDLERS = {
    "ingest": handle_ingest,
    "detect": handle_detect,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_handlers.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add bootleg/jobs/handlers.py tests/test_handlers.py
git commit -m "feat: add ingest and detect job handlers"
```

---

### Task 12: Inbox watcher

**Files:**
- Create: `bootleg/watcher.py`
- Test: `tests/test_watcher.py`

**Interfaces:**
- Consumes: `Library` (Task 1), `enqueue` (Task 10)
- Produces: `is_stable(path: Path, settle_s: float = 3.0, poll_s: float = 0.5) -> bool`; `scan_inbox(library, conn, *, settle_s: float = 3.0) -> list[str]` returning enqueued job ids; `InboxWatcher(library)` with `.start()` / `.stop()`; `VIDEO_SUFFIXES: frozenset[str]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_watcher.py`:

```python
import threading
import time
import pytest

from bootleg.db.schema import connect, migrate
from bootleg.watcher import is_stable, scan_inbox


@pytest.fixture
def conn(library):
    c = connect(library.db_path)
    migrate(c)
    return c


def test_is_stable_true_for_a_settled_file(library):
    f = library.inbox / "a.mov"
    f.write_bytes(b"x" * 1024)
    assert is_stable(f, settle_s=0.3, poll_s=0.1) is True


def test_is_stable_false_while_a_file_is_still_growing(library):
    f = library.inbox / "growing.mov"
    f.write_bytes(b"x" * 1024)
    stop = threading.Event()

    def grow():
        while not stop.is_set():
            with f.open("ab") as fh:
                fh.write(b"y" * 4096)
            time.sleep(0.05)

    t = threading.Thread(target=grow, daemon=True)
    t.start()
    try:
        assert is_stable(f, settle_s=0.4, poll_s=0.1) is False
    finally:
        stop.set()
        t.join()


def test_scan_enqueues_settled_videos(library, conn):
    (library.inbox / "a.mov").write_bytes(b"x" * 1024)
    ids = scan_inbox(library, conn, settle_s=0.2)
    assert len(ids) == 1
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (ids[0],)).fetchone()
    assert row["type"] == "ingest"


def test_scan_ignores_non_video_files(library, conn):
    (library.inbox / "notes.txt").write_text("hello")
    (library.inbox / ".DS_Store").write_bytes(b"junk")
    assert scan_inbox(library, conn, settle_s=0.2) == []


def test_scan_does_not_enqueue_the_same_file_twice(library, conn):
    (library.inbox / "a.mov").write_bytes(b"x" * 1024)
    scan_inbox(library, conn, settle_s=0.2)
    assert scan_inbox(library, conn, settle_s=0.2) == []


def test_scan_accepts_uppercase_suffixes(library, conn):
    (library.inbox / "IMG_0001.MOV").write_bytes(b"x" * 1024)
    assert len(scan_inbox(library, conn, settle_s=0.2)) == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_watcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bootleg.watcher'`

- [ ] **Step 3: Write `bootleg/watcher.py`**

```python
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from bootleg.config import Library
from bootleg.db import jobs as jobq
from bootleg.db.schema import connect, migrate

log = logging.getLogger(__name__)

VIDEO_SUFFIXES = frozenset({".mov", ".mp4", ".m4v", ".avi", ".mkv"})
SCAN_INTERVAL_S = 5.0


def is_stable(path: Path, settle_s: float = 3.0, poll_s: float = 0.5) -> bool:
    """True once the file size has not changed for settle_s.

    A half-copied 10 GB file probes fine and ingests into a corrupt session.
    This check is the only thing preventing that.
    """
    deadline = time.monotonic() + settle_s * 4
    last = -1
    unchanged_for = 0.0

    while time.monotonic() < deadline:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size == last:
            unchanged_for += poll_s
            if unchanged_for >= settle_s:
                return True
        else:
            unchanged_for = 0.0
            last = size
        time.sleep(poll_s)
    return False


def _already_queued(conn: sqlite3.Connection, path: Path) -> bool:
    rows = conn.execute(
        "SELECT payload FROM jobs WHERE type='ingest' AND status IN ('queued','running')"
    ).fetchall()
    return any(json.loads(r["payload"]).get("path") == str(path) for r in rows)


def scan_inbox(
    library: Library, conn: sqlite3.Connection, *, settle_s: float = 3.0
) -> list[str]:
    enqueued: list[str] = []
    for path in sorted(library.inbox.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        if _already_queued(conn, path):
            continue
        if not is_stable(path, settle_s=settle_s, poll_s=min(0.5, settle_s / 2)):
            log.info("still copying, skipping this pass: %s", path.name)
            continue
        enqueued.append(jobq.enqueue(conn, "ingest", {"path": str(path)}))
    return enqueued


class InboxWatcher:
    """Polls rather than using inotify — network and USB volumes fire
    filesystem events unreliably, and a 5 second poll is free."""

    def __init__(self, library: Library):
        self.library = library
        self.conn = connect(library.db_path)
        migrate(self.conn)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                scan_inbox(self.library, self.conn)
            except Exception:
                log.exception("inbox scan failed")
            self._stop.wait(SCAN_INTERVAL_S)

    def start(self) -> None:
        self.library.inbox.mkdir(exist_ok=True)
        self._thread = threading.Thread(target=self._loop, daemon=True, name="inbox")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_watcher.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add bootleg/watcher.py tests/test_watcher.py
git commit -m "feat: add inbox watcher with file-size stability check"
```

---

### Task 13: REST API and range-request media

**Files:**
- Create: `bootleg/api/__init__.py`
- Create: `bootleg/api/media.py`
- Create: `bootleg/api/routes.py`
- Create: `bootleg/api/app.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: everything from Tasks 1-12
- Produces: `range_response(path: Path, range_header: str | None) -> Response`; `create_app(library: Library) -> FastAPI`
- Endpoints:
  - `GET  /api/sessions` → `[{id, title, played_on, status, rally_count, starred_count}]`
  - `GET  /api/sessions/{id}` → `{session, sources: [...], rallies: [...]}`
  - `POST /api/rallies/{id}/star` body `{"starred": bool}` → `{ok: true}`
  - `POST /api/rallies/{id}/reject` body `{"rejected": bool}` → `{ok: true}`
  - `POST /api/rallies/{id}/bounds` body `{"start_ms": int, "end_ms": int}` → `{ok: true}`
  - `POST /api/sources/{id}/resegment` body `{"threshold": float}` → `{count: int}`
  - `GET  /api/jobs` → `[{id, type, status, progress, error}]`
  - `GET  /media/{session_id}/{idx}/proxy.mp4` → 200 or 206

- [ ] **Step 1: Write the failing test**

Create `tests/test_api.py`:

```python
import pytest
from fastapi.testclient import TestClient

from bootleg.api.app import create_app
from bootleg.db.rallies import list_rallies, replace_rallies
from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import add_source, find_or_create_session_for_date
from bootleg.detect.segment import Interval


@pytest.fixture
def conn(library):
    c = connect(library.db_path)
    migrate(c)
    return c


@pytest.fixture
def client(library, conn):
    return TestClient(create_app(library))


@pytest.fixture
def seeded(library, conn):
    session_id = find_or_create_session_for_date(conn, "2026-08-19")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-08-19T10:00:00Z", duration_ms=60_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_0001.MOV",
    )
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7)])
    return {"session_id": session_id, "source_id": source_id, "idx": idx}


def test_list_sessions_includes_counts(client, seeded):
    r = client.get("/api/sessions")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["rally_count"] == 2
    assert body[0]["starred_count"] == 0


def test_get_session_returns_sources_and_rallies(client, seeded):
    r = client.get(f"/api/sessions/{seeded['session_id']}")
    assert r.status_code == 200
    body = r.json()
    assert len(body["sources"]) == 1
    assert len(body["rallies"]) == 2
    assert body["rallies"][0]["idx"] == 1


def test_get_unknown_session_is_404(client):
    assert client.get("/api/sessions/nope").status_code == 404


def test_star_endpoint_updates_the_row(client, conn, seeded):
    rally_id = list_rallies(conn, seeded["session_id"])[0]["id"]
    assert client.post(f"/api/rallies/{rally_id}/star",
                       json={"starred": True}).status_code == 200
    assert list_rallies(conn, seeded["session_id"])[0]["starred"] == 1


def test_bounds_endpoint_does_not_touch_det_columns(client, conn, seeded):
    rally_id = list_rallies(conn, seeded["session_id"])[0]["id"]
    client.post(f"/api/rallies/{rally_id}/bounds",
                json={"start_ms": 1200, "end_ms": 4800})
    row = list_rallies(conn, seeded["session_id"])[0]
    assert (row["start_ms"], row["end_ms"]) == (1200, 4800)
    assert (row["det_start_ms"], row["det_end_ms"]) == (1000, 5000)


def test_bounds_rejects_inverted_range(client, conn, seeded):
    rally_id = list_rallies(conn, seeded["session_id"])[0]["id"]
    r = client.post(f"/api/rallies/{rally_id}/bounds",
                    json={"start_ms": 5000, "end_ms": 1000})
    assert r.status_code == 422


def test_jobs_endpoint_returns_a_list(client):
    r = client.get("/api/jobs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_media_returns_full_body_without_range_header(client, library, seeded):
    src_dir = library.source_dir(seeded["session_id"], seeded["idx"])
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "proxy.mp4").write_bytes(b"0123456789")

    r = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/proxy.mp4")
    assert r.status_code == 200
    assert r.content == b"0123456789"
    assert r.headers["accept-ranges"] == "bytes"


def test_media_serves_partial_content_for_a_range(client, library, seeded):
    src_dir = library.source_dir(seeded["session_id"], seeded["idx"])
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "proxy.mp4").write_bytes(b"0123456789")

    r = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/proxy.mp4",
                   headers={"Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.content == b"2345"
    assert r.headers["content-range"] == "bytes 2-5/10"


def test_media_open_ended_range(client, library, seeded):
    src_dir = library.source_dir(seeded["session_id"], seeded["idx"])
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "proxy.mp4").write_bytes(b"0123456789")

    r = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/proxy.mp4",
                   headers={"Range": "bytes=7-"})
    assert r.status_code == 206
    assert r.content == b"789"


def test_media_unsatisfiable_range_is_416(client, library, seeded):
    src_dir = library.source_dir(seeded["session_id"], seeded["idx"])
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "proxy.mp4").write_bytes(b"0123456789")

    r = client.get(f"/media/{seeded['session_id']}/{seeded['idx']}/proxy.mp4",
                   headers={"Range": "bytes=50-60"})
    assert r.status_code == 416


def test_media_missing_file_is_404(client, seeded):
    assert client.get(f"/media/{seeded['session_id']}/9/proxy.mp4").status_code == 404


def test_resegment_rewrites_rallies_from_cached_features(client, library, conn, seeded):
    from bootleg.detect.features import FeatureFrame, Player, write_features

    frames = [
        FeatureFrame(i * 200, 2,
                     Player(0.5, 0.9, 0.30, 2.5), Player(0.5, 0.4, 0.10, 2.5),
                     hits=1, hit_reg=0.9)
        for i in range(40)
    ]
    src_dir = library.source_dir(seeded["session_id"], seeded["idx"])
    src_dir.mkdir(parents=True, exist_ok=True)
    write_features(src_dir / "features.jsonl", frames)

    r = client.post(f"/api/sources/{seeded['source_id']}/resegment",
                    json={"threshold": 0.45})
    assert r.status_code == 200
    assert r.json()["count"] == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bootleg.api'`

- [ ] **Step 3: Write `bootleg/api/media.py`**

Create `bootleg/api/__init__.py` (empty file).

```python
import re
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
CHUNK = 1024 * 1024


def range_response(path: Path, range_header: str | None) -> Response:
    """Serve a file, honouring a single-range request.

    Without 206 support the <video> element cannot seek at all.
    """
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Not found: {path.name}")

    size = path.stat().st_size
    if not range_header:
        return FileResponse(path, headers={"Accept-Ranges": "bytes"})

    match = RANGE_RE.fullmatch(range_header.strip())
    if not match:
        raise HTTPException(status_code=416, detail="Malformed Range header")

    raw_start, raw_end = match.groups()
    if raw_start == "":
        if raw_end == "":
            raise HTTPException(status_code=416, detail="Malformed Range header")
        length = int(raw_end)
        start = max(0, size - length)
        end = size - 1
    else:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1

    if start >= size or end < start:
        raise HTTPException(
            status_code=416, detail="Range not satisfiable",
            headers={"Content-Range": f"bytes */{size}"},
        )
    end = min(end, size - 1)

    def stream():
        remaining = end - start + 1
        with path.open("rb") as fh:
            fh.seek(start)
            while remaining > 0:
                chunk = fh.read(min(CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        stream(),
        status_code=206,
        media_type="video/mp4",
        headers={
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
            "Accept-Ranges": "bytes",
        },
    )
```

- [ ] **Step 4: Write `bootleg/api/routes.py`**

```python
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, model_validator

from bootleg.db.rallies import list_rallies, replace_rallies, set_bounds, set_rejected, set_star
from bootleg.db.sessions import get_session, get_source, list_sessions, list_sources
from bootleg.detect.features import read_features
from bootleg.detect.segment import SegmentParams, segment

from .media import range_response

router = APIRouter()


class StarBody(BaseModel):
    starred: bool


class RejectBody(BaseModel):
    rejected: bool


class BoundsBody(BaseModel):
    start_ms: int
    end_ms: int

    @model_validator(mode="after")
    def check_order(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


class ResegmentBody(BaseModel):
    threshold: float = SegmentParams().threshold


def _conn(request: Request):
    return request.app.state.conn


def _library(request: Request):
    return request.app.state.library


@router.get("/api/sessions")
def api_list_sessions(request: Request):
    conn = _conn(request)
    out = []
    for s in list_sessions(conn):
        counts = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(starred),0) AS starred"
            " FROM rallies WHERE session_id = ? AND rejected = 0",
            (s["id"],),
        ).fetchone()
        out.append({
            **dict(s),
            "rally_count": counts["total"],
            "starred_count": counts["starred"],
        })
    return out


@router.get("/api/sessions/{session_id}")
def api_get_session(session_id: str, request: Request):
    conn = _conn(request)
    session = get_session(conn, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session": dict(session),
        "sources": [dict(r) for r in list_sources(conn, session_id)],
        "rallies": [dict(r) for r in list_rallies(conn, session_id)],
    }


@router.post("/api/rallies/{rally_id}/star")
def api_star(rally_id: str, body: StarBody, request: Request):
    set_star(_conn(request), rally_id, body.starred)
    return {"ok": True}


@router.post("/api/rallies/{rally_id}/reject")
def api_reject(rally_id: str, body: RejectBody, request: Request):
    set_rejected(_conn(request), rally_id, body.rejected)
    return {"ok": True}


@router.post("/api/rallies/{rally_id}/bounds")
def api_bounds(rally_id: str, body: BoundsBody, request: Request):
    set_bounds(_conn(request), rally_id, body.start_ms, body.end_ms)
    return {"ok": True}


@router.post("/api/sources/{source_id}/resegment")
def api_resegment(source_id: str, body: ResegmentBody, request: Request):
    conn = _conn(request)
    library = _library(request)
    source = get_source(conn, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    path = library.source_dir(source["session_id"], source["idx"]) / "features.jsonl"
    if not path.exists():
        raise HTTPException(status_code=409, detail="Source has not been detected yet")

    intervals = segment(read_features(path), SegmentParams(threshold=body.threshold))
    count = replace_rallies(conn, source["session_id"], source_id, intervals)
    return {"count": count}


@router.get("/api/jobs")
def api_jobs(request: Request):
    rows = _conn(request).execute(
        "SELECT id,type,status,progress,error,created_at,finished_at"
        " FROM jobs ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/media/{session_id}/{idx}/proxy.mp4")
def api_proxy(session_id: str, idx: int, request: Request,
              range: str | None = Header(default=None)):
    path = _library(request).source_dir(session_id, idx) / "proxy.mp4"
    return range_response(path, range)
```

- [ ] **Step 5: Write `bootleg/api/app.py`**

```python
from fastapi import FastAPI

from bootleg.config import Library
from bootleg.db.schema import connect, migrate

from .routes import router


def create_app(library: Library) -> FastAPI:
    app = FastAPI(title="BootlegVision", version="0.1.0")
    conn = connect(library.db_path)
    migrate(conn)
    app.state.library = library
    app.state.conn = conn
    app.include_router(router)
    return app
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_api.py -v`
Expected: 13 passed

- [ ] **Step 7: Commit**

```bash
git add bootleg/api tests/test_api.py
git commit -m "feat: add REST API with range-request media serving"
```

---

### Task 14: CLI

**Files:**
- Create: `bootleg/cli.py`
- Modify: `tests/test_api.py` — no change needed; CLI is exercised manually
- Create: `README.md`

**Interfaces:**
- Consumes: everything
- Produces: `main(argv: list[str] | None = None) -> int`; subcommands `serve`, `ingest`, `detect`, `segment`, `doctor`

- [ ] **Step 1: Write `bootleg/cli.py`**

```python
import argparse
import json
import logging
import sys
from pathlib import Path

from bootleg.config import Library, LibraryNotMounted
from bootleg.db import jobs as jobq
from bootleg.db.rallies import list_rallies
from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import get_source
from bootleg.detect.features import read_features
from bootleg.detect.segment import SegmentParams, segment
from bootleg.jobs.handlers import HANDLERS
from bootleg.jobs.worker import Worker
from bootleg.watcher import InboxWatcher


def _library(args) -> Library:
    return Library.open(Path(args.library).expanduser())


def cmd_doctor(args) -> int:
    from bootleg.accel import detect_accel

    accel = detect_accel()
    lib = _library(args)
    print(f"library:      {lib.root}")
    print(f"hwaccel:      {accel.hwaccel or 'none (software)'}")
    print(f"h264 encoder: {accel.h264_encoder}")
    print(f"torch device: {accel.torch_device}")
    for sub in (lib.inbox, lib.sessions_dir, lib.reels_dir):
        print(f"{'ok ' if sub.is_dir() else 'MISSING'} {sub}")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from bootleg.api.app import create_app

    lib = _library(args)
    app = create_app(lib)

    worker = Worker(lib, HANDLERS)
    watcher = InboxWatcher(lib)
    worker.start()
    watcher.start()

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        watcher.stop()
        worker.stop()
    return 0


def cmd_ingest(args) -> int:
    lib = _library(args)
    conn = connect(lib.db_path)
    migrate(conn)
    job_id = jobq.enqueue(conn, "ingest", {"path": str(Path(args.path).resolve())})
    print(f"queued ingest {job_id}")
    if args.now:
        Worker(lib, HANDLERS).run_once()
        print("done")
    return 0


def cmd_detect(args) -> int:
    lib = _library(args)
    conn = connect(lib.db_path)
    migrate(conn)
    payload = {"source_id": args.source_id, "reuse_features": args.reuse_features}
    jobq.enqueue(conn, "detect", payload)
    if args.now:
        Worker(lib, HANDLERS).run_once()
    return 0


def cmd_segment(args) -> int:
    """Re-run segmentation on cached features and print the result.

    The tuning loop: change a threshold, see the rally count, no YOLO.
    """
    lib = _library(args)
    conn = connect(lib.db_path)
    migrate(conn)
    source = get_source(conn, args.source_id)
    if source is None:
        print(f"no such source: {args.source_id}", file=sys.stderr)
        return 1

    path = lib.source_dir(source["session_id"], source["idx"]) / "features.jsonl"
    frames = read_features(path)
    params = SegmentParams(threshold=args.threshold)
    intervals = segment(frames, params)

    if args.dry_run:
        for i, iv in enumerate(intervals, 1):
            print(f"{i:3d}  {iv.start_ms/1000:8.2f}s → {iv.end_ms/1000:8.2f}s"
                  f"  ({(iv.end_ms-iv.start_ms)/1000:5.1f}s)  conf {iv.confidence:.2f}")
        print(f"\n{len(intervals)} rallies at threshold {args.threshold}")
        return 0

    from bootleg.db.rallies import replace_rallies

    replace_rallies(conn, source["session_id"], args.source_id, intervals)
    print(f"wrote {len(intervals)} rallies")
    print(json.dumps([dict(r) for r in list_rallies(conn, source["session_id"])],
                     indent=2)[:2000])
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(prog="bootleg")
    parser.add_argument("--library", required=True, help="path to the library root")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="show detected hardware and library state")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("serve", help="run the web server, worker and inbox watcher")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8420)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("ingest", help="queue a video file for ingest")
    p.add_argument("path")
    p.add_argument("--now", action="store_true", help="run the job immediately")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("detect", help="queue detection for a source")
    p.add_argument("source_id")
    p.add_argument("--reuse-features", action="store_true")
    p.add_argument("--now", action="store_true")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("segment", help="re-segment cached features")
    p.add_argument("source_id")
    p.add_argument("--threshold", type=float, default=SegmentParams().threshold)
    p.add_argument("--dry-run", action="store_true",
                   help="print intervals without writing rallies")
    p.set_defaults(func=cmd_segment)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except LibraryNotMounted as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify the CLI parses and reports**

Run:
```bash
mkdir -p /tmp/bl/{_inbox,sessions,reels}
python -m bootleg.cli --library /tmp/bl doctor
```
Expected: prints the library path, an hwaccel line, an encoder, a torch device, and three `ok` lines.

- [ ] **Step 3: Verify the missing-library error path**

Run: `python -m bootleg.cli --library /tmp/definitely-not-mounted doctor`
Expected: exit code 2, message `error: Library root not found: /tmp/definitely-not-mounted. Is the drive plugged in?`

- [ ] **Step 4: Write `README.md`**

````markdown
# BootlegVision

Local tennis rally cutter. Ingests phone footage, segments it into rallies,
serves them over a REST API.

## Setup

```bash
conda create -n bootleg python=3.12 -y
conda activate bootleg
pip install -e ".[dev]"
brew install ffmpeg
```

## Library

The library is a self-contained folder, normally on an external drive:

```
/Volumes/BootlegVision/
  library.db
  _inbox/            drop videos here
  sessions/<date>/sources/NN/{original,proxy.mp4,thumbs.jpg,features.jsonl}
  reels/
```

Create it once by hand — the app never creates it, so a missing drive is an
error instead of a silent second library on internal storage.

```bash
mkdir -p /Volumes/BootlegVision/{_inbox,sessions,reels}
```

## Use

```bash
bootleg --library /Volumes/BootlegVision doctor      # check hardware + paths
bootleg --library /Volumes/BootlegVision serve       # http://127.0.0.1:8420
```

Drop a video in `_inbox/`. It is picked up within 5 seconds once the file
stops growing, transcoded to a 1080p proxy, and detected automatically.

## Tuning segmentation

Detection caches features to `features.jsonl`, so re-segmenting costs
milliseconds and needs no GPU:

```bash
bootleg --library /Volumes/BootlegVision segment <source_id> --threshold 0.35 --dry-run
```

## Tests

```bash
pytest -v
```

Tests never run YOLO. Detector output is fixtured or mocked throughout.
````

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass (approximately 90 across 11 files)

- [ ] **Step 6: Commit**

```bash
git add bootleg/cli.py README.md
git commit -m "feat: add CLI with serve, ingest, detect, segment and doctor"
```

---

### Task 15: End-to-end smoke test

**Files:**
- Create: `tests/test_e2e.py`

**Interfaces:**
- Consumes: everything
- Produces: nothing importable — this task exists to prove the wiring holds

- [ ] **Step 1: Write the failing test**

Create `tests/test_e2e.py`:

```python
import subprocess
import pytest
from fastapi.testclient import TestClient

from bootleg.api.app import create_app
from bootleg.db.schema import connect, migrate
from bootleg.db.sessions import list_sessions, list_sources
from bootleg.detect.features import FeatureFrame, Player, write_features
from bootleg.jobs.handlers import HANDLERS
from bootleg.jobs.worker import Worker
from bootleg.watcher import scan_inbox


@pytest.fixture
def conn(library):
    c = connect(library.db_path)
    migrate(c)
    return c


def test_drop_file_then_serve_it_over_the_api(library, conn):
    # 1. a file lands in the inbox
    dropped = library.inbox / "IMG_0007.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=640x360:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(dropped)],
        check=True, capture_output=True,
    )

    # 2. the watcher queues it
    assert len(scan_inbox(library, conn, settle_s=0.2)) == 1

    # 3. the worker ingests it
    worker = Worker(library, HANDLERS)
    assert worker.run_once() is True

    session = list_sessions(conn)[0]
    source = list_sources(conn, session["id"])[0]
    src_dir = library.source_dir(session["id"], source["idx"])
    assert (src_dir / "proxy.mp4").exists()

    # 4. stand in for YOLO with fixtured features, then run detect
    write_features(src_dir / "features.jsonl", [
        FeatureFrame(i * 200, 2,
                     Player(0.5, 0.9, 0.30, 2.5), Player(0.5, 0.4, 0.10, 2.5),
                     hits=1, hit_reg=0.9)
        for i in range(40)
    ])
    HANDLERS["detect"](library, {"source_id": source["id"], "reuse_features": True})

    # 5. the API exposes the result
    client = TestClient(create_app(library))
    body = client.get(f"/api/sessions/{session['id']}").json()
    assert len(body["rallies"]) == 1

    # 6. and the proxy streams with range support
    r = client.get(f"/media/{session['id']}/{source['idx']}/proxy.mp4",
                   headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert len(r.content) == 100
```

- [ ] **Step 2: Run it**

Run: `pytest tests/test_e2e.py -v`
Expected: 1 passed

- [ ] **Step 3: Run the whole suite and lint**

Run:
```bash
pytest -q && ruff check bootleg tests
```
Expected: all tests pass, ruff reports no errors.

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test: add end-to-end inbox-to-api smoke test"
```

---

## First real footage — the tuning loop

Plan 1 is done when this works on an actual recording. It is not a task because it needs your video, but it is the point of everything above.

1. `bootleg --library /Volumes/BootlegVision serve`
2. Drop one session into `_inbox/`. Wait for detection.
3. `bootleg ... segment <source_id> --dry-run` — read the interval list against what you remember of the session.
4. Sweep the threshold: `--threshold 0.25`, `0.35`, `0.45`, `0.55`. Watch the rally count.
5. Pick the value that **over-segments slightly**. Recall over precision — a false rally is one keystroke in Plan 2, a missed rally is gone.
6. Commit that threshold as the new `SegmentParams` default, and copy that source's `features.jsonl` to `tests/fixtures/golden-<date>.jsonl` with your hand-labeled intervals beside it. That becomes the regression guard for every future weight change.

`w1`-`w6` stay at their defaults until there is labeled data from Plan 2's review pass. Do not hand-tune six weights against one session by eye — that overfits to one afternoon of tennis.

---

## Deferred to later plans

**Plan 2 — Review UI:** Svelte SPA, queue mode with dual-`<video>` preload, timeline with three bands and the score curve, court quad editor, re-segment slider, keyboard bindings, undo.

**Plan 3 — Library and reels:** 4K clip export with the locked libx264 profile, concat with `-c copy` plus duration validation, rally browser with filters, reel builder, Reclaim Space with the starred-clip guard.
