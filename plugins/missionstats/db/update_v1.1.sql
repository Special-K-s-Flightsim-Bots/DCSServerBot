UPDATE plugins SET version = 'v1.2' WHERE plugin = 'missionstats';
ALTER TABLE missionstats ADD COLUMN id SERIAL PRIMARY KEY;
CREATE INDEX IF NOT EXISTS idx_missionstats_target_id ON missionstats(target_id);
