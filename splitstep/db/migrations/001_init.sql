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
