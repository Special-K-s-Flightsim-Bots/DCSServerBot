DELETE FROM strafe_runs WHERE player_ucid NOT IN (SELECT ucid FROM players);
ALTER TABLE strafe_runs ADD CONSTRAINT strafe_runs_player_ucid_fkey FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE;
DELETE FROM bomb_runs WHERE player_ucid NOT IN (SELECT ucid FROM players);
ALTER TABLE bomb_runs ADD CONSTRAINT bomb_runs_player_ucid_fkey FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE;
