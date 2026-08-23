-- det_start_ms/det_end_ms become nullable, and NULL acquires a meaning:
-- "no detector ever proposed this rally; a human made it."
--
-- This exists so timeline mode can cut one rally in two. The detector
-- merges two rallies whenever the break between them is too short to score
-- below threshold -- correctly, since the alternative tuning shreds real
-- rallies -- and until now the reviewer had no way to disagree, because
-- moving a boundary needs a second rally to move it TO.
--
-- Why the second half carries no det span, rather than inheriting the
-- first's: rally_labels anchors on (source_id, det_start_ms, det_end_ms) --
-- the detector's own span, which is what lets the corpus survive
-- replace_rallies (see 003). Two halves sharing one det span would be the
-- same row in the corpus, and labelling the second would silently overwrite
-- the judgement on the first. Giving each half its own det span covering
-- its own bounds is worse still: det_* is immutable and records what the
-- detector ORIGINALLY GUESSED, so writing spans it never produced
-- fabricates training data and feeds `labels score` candidates with no
-- basis in any detector run.
--
-- The absence of a span is the marker rather than a boolean beside it,
-- because a boolean can drift out of agreement with the columns it
-- describes and an absence cannot. Every consumer that needs to ask "is
-- this a detector proposal?" already reads det_*, and now gets a truthful
-- answer without knowing splits exist.
--
-- Rebuilding `rallies` is safe: nothing in the schema declares REFERENCES
-- rallies. reel_items keys on the span (007) and rally_labels.rally_id
-- deliberately carries no foreign key (003) -- both for the same reason,
-- that replace_rallies deletes every rally for a source on each sweep.
CREATE TABLE rallies_new (
  id            TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  source_id     TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  idx           INTEGER NOT NULL,
  start_ms      INTEGER NOT NULL,
  end_ms        INTEGER NOT NULL,
  det_start_ms  INTEGER,
  det_end_ms    INTEGER,
  confidence    REAL    NOT NULL,
  starred       INTEGER NOT NULL DEFAULT 0,
  rejected      INTEGER NOT NULL DEFAULT 0,
  point         INTEGER NOT NULL DEFAULT 0,
  reviewed_at   TEXT,
  clip_path     TEXT,
  note          TEXT NOT NULL DEFAULT '',
  seen_at       TEXT,

  -- Both or neither, never one. A half-present det span is a third state
  -- nothing knows how to read: `det_start_ms IS NULL` is the question every
  -- consumer asks, and it must answer for the pair.
  CHECK ((det_start_ms IS NULL) = (det_end_ms IS NULL)),
  UNIQUE(session_id, idx)
);

INSERT INTO rallies_new (id, session_id, source_id, idx, start_ms, end_ms,
  det_start_ms, det_end_ms, confidence, starred, rejected, point, reviewed_at,
  clip_path, note, seen_at)
SELECT id, session_id, source_id, idx, start_ms, end_ms,
  det_start_ms, det_end_ms, confidence, starred, rejected, point, reviewed_at,
  clip_path, note, seen_at
FROM rallies;

DROP TABLE rallies;
ALTER TABLE rallies_new RENAME TO rallies;

CREATE INDEX idx_rallies_session ON rallies(session_id, idx);
CREATE INDEX idx_rallies_source  ON rallies(source_id);
CREATE INDEX idx_rallies_starred ON rallies(starred) WHERE starred = 1;
CREATE INDEX idx_rallies_point   ON rallies(point) WHERE point = 1;
