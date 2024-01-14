ALTER TABLE missionstats ADD COLUMN IF NOT EXISTS id SERIAL PRIMARY KEY;
CREATE INDEX IF NOT EXISTS idx_missionstats_target_id ON missionstats(target_id);
