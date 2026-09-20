CREATE INDEX IF NOT EXISTS idx_missionstats_init_id_time on missionstats (init_id, time);
CREATE INDEX IF NOT EXISTS idx_missionstats_target_id_time on missionstats (target_id, time);
CREATE INDEX IF NOT EXISTS idx_refuelingstats_init_id_time on refuelingstats(init_id, time);
