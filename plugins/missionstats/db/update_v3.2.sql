UPDATE missionstats SET init_id = NULL WHERE init_id = '-1';
UPDATE missionstats SET target_id = NULL WHERE target_id = '-1';
DELETE FROM missionstats WHERE init_id IS NOT NULL AND init_id NOT IN (SELECT ucid FROM players);
DELETE FROM missionstats WHERE target_id IS NOT NULL AND target_id NOT IN (SELECT ucid FROM players);
DELETE FROM missionstats WHERE mission_id NOT IN (SELECT id FROM missions);
ALTER TABLE missionstats ADD CONSTRAINT missionstats_init_id_fkey FOREIGN KEY (init_id) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE;
ALTER TABLE missionstats ADD CONSTRAINT missionstats_target_id_fkey FOREIGN KEY (target_id) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE;
ALTER TABLE missionstats ADD CONSTRAINT missionstats_mission_id_fkey FOREIGN KEY (mission_id) REFERENCES missions (id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_missionstats_event_init_id on missionstats (event, init_id);
