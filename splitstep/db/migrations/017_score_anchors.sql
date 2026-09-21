-- Score anchors: a reviewer's "entering this point in the source, the
-- score is X".
--
-- Score in this app is replayed from rallies.winner in idx order and stored
-- nowhere (migration 013). An editable scoreboard would be a second source
-- of truth that goes stale the moment the rally list changes, so the fix
-- for a wrong score is not to edit the board but to give the replay a new
-- starting point: from an anchor, score() continues normally over the
-- winners that follow. An anchor is a stored INPUT to the replay, exactly
-- like a winner is -- never a stored output.
--
-- Keyed on (source_id, at_ms) -- a time on a source -- never on a rally
-- id, and with no rally foreign key. This is the reel_items lesson
-- (migration 007): replace_rallies deletes every rally for a source on
-- each threshold sweep, so anything keyed to a rally id evaporates on the
-- first re-segment, and rally_labels (003) deliberately carries no foreign
-- key for the same reason. A time survives the sweep. The boundaries
-- around it may move, which is why splitstep/score.py::_place_anchors
-- attaches an anchor to the rally CONTAINING it rather than the one
-- starting at it, but the footage at that instant is the same footage.
--
-- The cascade on source_id is the one deliberate cascade, for the same
-- reason reel_items cascades on it: no footage, no time in it to anchor.
--
-- state is JSON -- {"sets": [[6, 4]], "games": [3, 2], "points": [2, 1]} --
-- with points as RAW COUNTS, not labels: 40-40 is [3, 3] and Ad-40 is
-- [4, 3], the same numbers the replay keeps internally. Three fields and
-- only three; in_tiebreak, finished, the server and the service-unit count
-- are all derived from them plus the session's rules, and a stored copy of
-- any of them could disagree with the numbers it describes. One JSON
-- column rather than three, mirroring sessions.scoring: the shape is
-- validated by splitstep.score.anchor_from_dict, one validator for the
-- CLI, the column and the route, exactly as rules_from_dict is for
-- scoring.
CREATE TABLE score_anchors (
  source_id  TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  at_ms      INTEGER NOT NULL,
  state      TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (source_id, at_ms)
);
