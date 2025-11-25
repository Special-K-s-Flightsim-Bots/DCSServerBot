DELETE FROM players_hist WHERE ucid NOT IN (SELECT ucid FROM players);
ALTER TABLE players_hist ADD CONSTRAINT players_hist_ucid_fkey FOREIGN KEY (ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE;
DELETE FROM watchlist WHERE player_ucid NOT IN (SELECT ucid FROM players);
ALTER TABLE watchlist ADD CONSTRAINT watchlist_player_ucid_fkey FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE;
DELETE FROM missions WHERE server_name NOT IN (SELECT server_name FROM servers);
ALTER TABLE missions ADD CONSTRAINT missions_server_name_fkey FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE;
