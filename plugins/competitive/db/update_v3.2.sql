DELETE FROM trueskill WHERE player_ucid NOT IN (SELECT ucid FROM players);
ALTER TABLE trueskill ADD CONSTRAINT trueskill_player_ucid_fkey FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE;
DELETE FROM trueskill_hist WHERE player_ucid NOT IN (SELECT ucid FROM players);
ALTER TABLE trueskill_hist ADD CONSTRAINT trueskill_hist_player_ucid_fkey FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE;
