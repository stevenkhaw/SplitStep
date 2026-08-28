-- Label mode's `U` needs a durable way to say "the reviewer took that
-- judgement back; this span currently has no verdict". Before this, undoing a
-- first-time label only cleared the client's own map: the reviewer was left
-- looking at an unlabelled clip while `latest_labels` still returned 'clean',
-- and a reload brought the retracted verdict straight back.
--
-- It is a new row rather than a DELETE or an UPDATE for the same reason
-- re-labelling is: clip #1's hand label was wrong once and the correction was
-- itself the finding, so nothing in this table is ever erased. A retraction is
-- one more append that supersedes what came before.
--
-- The row it writes carries neither a verdict nor (necessarily) a corrected
-- span, which the 003 table-level CHECK forbade outright. sqlite cannot ALTER
-- a CHECK, so the table is rebuilt: same columns, same FK, same index, plus
-- `retracted` and a CHECK that admits an empty row ONLY when it is an explicit
-- retraction. Everything else -- an accidental all-NULL insert from a caller
-- bug -- still fails loudly at the database, which is what 003's CHECK was for.
--
-- No PRAGMA foreign_keys toggling here on purpose. Toggling it inside a
-- migration would leak: `migrate()` runs on the same long-lived connection the
-- app then serves from, so an OFF that is never restored disables enforcement
-- for the whole process. The rebuild does not need it -- rally_labels is a
-- child table nothing else references, and every copied row already satisfies
-- the FK it is copied under.
CREATE TABLE rally_labels_new (
  id             TEXT PRIMARY KEY,
  source_id      TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  span_start_ms  INTEGER NOT NULL,
  span_end_ms    INTEGER NOT NULL,

  verdict        TEXT CHECK (verdict IS NULL OR
                             verdict IN ('clean','not_play','partly','unsure')),
  boundary_flags TEXT NOT NULL DEFAULT '',
  true_start_ms  INTEGER,
  true_end_ms    INTEGER,

  -- Provenance only, and DELIBERATELY NOT A FOREIGN KEY -- see 003.
  rally_id       TEXT,

  labelled_at    TEXT NOT NULL,

  -- 1 on a row that withdraws whatever the span's previous row asserted.
  -- Not merely decorative: it is what separates "the reviewer took their
  -- verdict back" from "a boundary drag wrote a verdict-less row", which are
  -- otherwise the same shape, and what the relaxed CHECK below keys on.
  retracted      INTEGER NOT NULL DEFAULT 0 CHECK (retracted IN (0,1)),

  -- A row must carry a verdict, a corrected span, or be a retraction. The
  -- third arm is the only thing 003 did not allow; an empty row that is not a
  -- retraction is still meaningless and still rejected.
  CHECK (verdict IS NOT NULL OR true_start_ms IS NOT NULL OR retracted = 1),
  -- A retraction asserts that there is no current verdict for this span.
  -- Letting it carry one would make the row say both things at once.
  CHECK (retracted = 0 OR verdict IS NULL)
);

INSERT INTO rally_labels_new
  (id, source_id, span_start_ms, span_end_ms, verdict, boundary_flags,
   true_start_ms, true_end_ms, rally_id, labelled_at, retracted)
SELECT id, source_id, span_start_ms, span_end_ms, verdict, boundary_flags,
       true_start_ms, true_end_ms, rally_id, labelled_at, 0
  FROM rally_labels;

DROP TABLE rally_labels;
ALTER TABLE rally_labels_new RENAME TO rally_labels;

CREATE INDEX idx_rally_labels_source ON rally_labels(source_id, span_start_ms);
