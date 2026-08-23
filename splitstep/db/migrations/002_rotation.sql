-- Clockwise degrees applied to a source's coded frame when its proxy is
-- built. Existing rows default to 0 and are deliberately NOT backfilled
-- from their originals' display matrices: their proxies were encoded under
-- ffmpeg's autorotate, so an inferred value would describe an intent the
-- file on disk does not match. Re-run setup on such a source instead.
ALTER TABLE sources ADD COLUMN rotation_deg INTEGER NOT NULL DEFAULT 0;
