DELETE FROM statistics WHERE player_ucid IN (SELECT ucid FROM players WHERE last_seen IS NULL);
DELETE FROM players WHERE last_seen IS NULL;
CREATE OR REPLACE FUNCTION player_hist_change() RETURNS trigger AS $$ BEGIN INSERT INTO players_hist(ucid, discord_id, name, coalition, manual, time) SELECT OLD.ucid, OLD.discord_id, OLD.name, OLD.coalition, OLD.manual, OLD.last_seen; RETURN NEW; END; $$ LANGUAGE 'plpgsql';
UPDATE players SET ipaddr = NULL;
UPDATE players_hist SET ipaddr = NULL;
