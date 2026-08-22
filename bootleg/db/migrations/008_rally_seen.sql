-- Splits "a human has seen this rally" from "a human passed judgement on
-- it" -- reviewed_at was carrying both meanings, and only the second one is
-- what reviewed_at's own writers (set_star/set_point/set_rejected) actually
-- stamp. The queue used reviewed_at IS NULL to find where a reviewer left
-- off, but persist.ts's skip case deliberately writes nothing on a plain
-- right-arrow (see its comment) -- stamping reviewed_at there would flip a
-- whole session to 'reviewed' off the back of a rally nobody judged. The
-- result: a rally skipped-but-never-judged keeps reviewed_at NULL forever,
-- and every reopened queue lands right back on it, no matter how far past
-- it the reviewer has actually looked.
--
-- seen_at answers "has a human looked at this" and is stamped by skip too
-- (see set_seen); reviewed_at keeps meaning "a human ruled on this" and
-- still drives session status alone -- see refresh_session_review_status,
-- deliberately left untouched by this change.
ALTER TABLE rallies ADD COLUMN seen_at TEXT;

-- Backfill, not a fresh NULL column: every rally already carrying a
-- reviewed_at was seen at exactly that moment -- a ruling cannot be made on
-- a rally nobody looked at. Without this, every existing library's next
-- queue open would regress to rally 1, a one-time replay of the exact bug
-- this migration exists to fix.
UPDATE rallies SET seen_at = reviewed_at WHERE reviewed_at IS NOT NULL;
