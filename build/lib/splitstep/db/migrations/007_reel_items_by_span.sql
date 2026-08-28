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
