-- A third review flag beside starred and rejected.
--
--   rejected  not a rally at all -- a bad detection
--   point     a point was played out, whatever the quality
--   starred   a highlight worth showing
--
-- The three are independent booleans. starred is expected to be a subset of
-- point in practice but is deliberately not constrained to be: a warm-up rally
-- can be worth watching without being a point, and enforcing containment would
-- make the reviewer argue with the tool.
ALTER TABLE rallies ADD COLUMN point INTEGER NOT NULL DEFAULT 0;

-- One-time reinterpretation of existing data, and it is a reinterpretation
-- rather than a copy. Until now a star meant "a point was played out" -- the
-- first real session starred all 24 points of a filmed tiebreaker, double
-- faults and missed returns included, because star was the only mark available
-- and "this is a point" was the thing worth recording. Moving that meaning to
-- `point` leaves `starred` free to mean "a highlight I like", which is what it
-- was always supposed to mean.
--
-- Stars are cleared rather than left set. If the second pass never happens, an
-- empty highlight set is honest and a set that still silently means "point" is
-- not. PRAGMA user_version guarantees this runs exactly once.
UPDATE rallies SET point = 1 WHERE starred = 1;
UPDATE rallies SET starred = 0;

CREATE INDEX idx_rallies_point ON rallies(point) WHERE point = 1;
