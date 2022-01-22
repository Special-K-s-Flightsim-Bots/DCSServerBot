UPDATE plugins SET version = 'v1.1' WHERE plugin = 'missionstats';
CREATE TABLE IF NOT EXISTS missionstats (mission_id INTEGER NOT NULL, event TEXT NOT NULL, init_id TEXT NOT NULL, init_side TEXT NOT NULL, init_type TEXT NOT NULL, init_cat TEXT NOT NULL, target_id TEXT, target_side TEXT, target_type TEXT, target_cat TEXT, weapon TEXT, time TIMESTAMP NOT NULL DEFAULT NOW());
CREATE INDEX IF NOT EXISTS idx_missionstats_init_id ON missionstats(init_id);
