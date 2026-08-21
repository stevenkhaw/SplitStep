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
