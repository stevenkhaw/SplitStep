-- A short caption per reel item ("match point"), burned beneath the "3/20"
-- counter in a numbered render and shown in the preview. On reel_items, not
-- on the clip: clips are shared across reels (same span can be #3 in one
-- reel and #11 in another) so the note belongs to the membership row, which
-- already survives re-segments by being keyed on the span.
ALTER TABLE reel_items ADD COLUMN note TEXT NOT NULL DEFAULT '';
