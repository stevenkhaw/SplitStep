-- Per-library key/value settings. First key: clip_color_profile, locking
-- the clip colour profile to the library instead of to a module constant
-- pinned to one specific phone. No backfill: resolution at first export
-- handles pre-existing libraries (see handlers._clip_color_profile).
CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
