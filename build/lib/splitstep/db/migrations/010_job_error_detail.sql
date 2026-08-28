-- The error column held a six-frame traceback, rendered raw into a 72px
-- badge panel. Split it: `error` becomes the one human sentence the UI
-- shows, `error_detail` keeps the traceback for whoever needs it. Existing
-- failed rows keep their traceback in `error` -- stale but harmless, and a
-- retry (also new in this phase) rewrites both columns.
ALTER TABLE jobs ADD COLUMN error_detail TEXT;
