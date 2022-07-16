CREATE TABLE players_hist (id SERIAL PRIMARY KEY, ucid TEXT NOT NULL, discord_id BIGINT NOT NULL, name TEXT NOT NULL, ipaddr TEXT NOT NULL, coalition TEXT, manual BOOLEAN NOT NULL, time TIMESTAMP NOT NULL DEFAULT NOW());
CREATE INDEX idx_players_hist_discord_id ON players_hist(discord_id);
CREATE INDEX idx_players_hist_ucid ON players_hist(ucid);
CREATE OR REPLACE FUNCTION player_hist_change() RETURNS trigger AS $$ BEGIN INSERT INTO players_hist(ucid, discord_id, name, ipaddr, coalition, manual) SELECT OLD.ucid, OLD.discord_id, OLD.name, OLD.ipaddr, OLD.coalition, OLD.manual; RETURN NEW; END; $$ LANGUAGE 'plpgsql';
CREATE TRIGGER tgr_player_update AFTER UPDATE OF discord_id, name, ipaddr, coalition, manual ON players FOR EACH ROW EXECUTE PROCEDURE player_hist_change();
