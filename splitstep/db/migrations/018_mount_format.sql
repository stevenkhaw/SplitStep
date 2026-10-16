-- Where the camera was mounted, what was being played, where the court's
-- corners are, and which scorer last cut the rallies.
--
-- mount and play_format are NOT NULL DEFAULT '' rather than nullable. "Not
-- set" is a real choice the reviewer can leave standing -- the wizard offers
-- it as a button -- and every source ingested before this migration is
-- exactly that. Making it '' means existing rows read as the choice they
-- already embody with no back-fill, and a NULL-vs-'' distinction never has
-- to be remembered by a reader. The allowed values ('fence'/'ground',
-- 'singles'/'doubles'/'drills') are enforced in set_source_setup, not by a
-- CHECK: SQLite cannot alter a CHECK later, and the vocabulary is the part
-- most likely to grow.
--
-- court_corners is JSON (four [x, y] pairs: doubles sidelines x baselines,
-- in the order the wizard collects them) or NULL, and is deliberately
-- separate from court_preset_id. The preset is the play REGION the
-- detector filters boxes by -- a margin-padded shape derived from the
-- corners. The corners are what the reviewer actually clicked, and the
-- fence scorer needs them undistorted; recovering them from a padded
-- region would be guessing. NULL means none were given.
--
-- detected_scorer records which scorer ('fence', 'subject', 'pair') cut
-- the source's CURRENT rallies. The Detection panel compares it with the
-- scorer the current mount/format would pick, and offers a re-run when they
-- differ -- the same idea as features_preset_id in 016, and for the same
-- reason: a timestamp or a "settings changed" flag cannot say whether the
-- rallies on screen came from the scorer now in force. NULL means unknown
-- (a source detected before this migration), rendered as unknown and never
-- as a guess.
ALTER TABLE sources ADD COLUMN mount TEXT NOT NULL DEFAULT '';
ALTER TABLE sources ADD COLUMN play_format TEXT NOT NULL DEFAULT '';
ALTER TABLE sources ADD COLUMN court_corners TEXT;
ALTER TABLE sources ADD COLUMN detected_scorer TEXT;
