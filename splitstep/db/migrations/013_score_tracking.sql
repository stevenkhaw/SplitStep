-- Match-score tracking. Two columns, and only two: the winner of each
-- point, and the session's rules. Everything a scoreboard shows is replayed
-- from winners in idx order by splitstep/score.py (and its TypeScript
-- twin), so nothing about the score itself is ever stored -- a corrected
-- winner or a re-segment recomputes for free instead of going stale.
--
-- winner: '' / 'a' / 'b'. Empty string, not NULL, following `note`: absence
-- has one representation. Positional rather than a name so renaming a
-- player touches no rally row.
ALTER TABLE rallies ADD COLUMN winner TEXT NOT NULL DEFAULT '';

-- scoring: '' when the session does not track a score, otherwise the rules
-- as JSON ({"players": [..], "sets": 3, "ad": true, "tiebreak": "at6",
-- "tiebreakTo": 7}). Per session, not per library: a match is a session.
ALTER TABLE sessions ADD COLUMN scoring TEXT NOT NULL DEFAULT '';
