DELETE FROM statistics WHERE mission_id NOT IN (SELECT id FROM missions);
DELETE FROM statistics WHERE player_ucid NOT IN (SELECT ucid FROM players);
ALTER TABLE statistics ADD CONSTRAINT statistics_mission_id_fkey FOREIGN KEY (mission_id) REFERENCES missions (id) ON DELETE CASCADE;
ALTER TABLE statistics ADD CONSTRAINT statistics_player_ucid_fkey FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE;
DELETE FROM squadron_members WHERE squadron_id NOT IN (SELECT id FROM squadrons);
DELETE FROM squadron_members WHERE player_ucid NOT IN (SELECT ucid FROM players);
ALTER TABLE squadron_members ADD CONSTRAINT squadron_members_squadron_id_fkey FOREIGN KEY (squadron_id) REFERENCES squadrons (id) ON DELETE CASCADE;
ALTER TABLE squadron_members ADD CONSTRAINT squadron_members_player_ucid_fkey FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_statistics ON mv_statistics (player_ucid, server_name, slot, side, tail_no);
