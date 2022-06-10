ALTER TABLE missionstats ADD COLUMN id SERIAL PRIMARY KEY;
CREATE INDEX IF NOT EXISTS idx_missionstats_target_id ON missionstats(target_id);
