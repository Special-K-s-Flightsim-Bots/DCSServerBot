DELETE FROM traps WHERE mission_id NOT IN (SELECT id FROM missions);
DELETE FROM traps WHERE player_ucid NOT IN (SELECT ucid FROM players);
ALTER TABLE traps ADD CONSTRAINT traps_mission_id_fkey FOREIGN KEY (mission_id) REFERENCES missions (id) ON DELETE CASCADE;
ALTER TABLE traps ADD CONSTRAINT traps_player_ucid_fkey FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE;
