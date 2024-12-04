DROP TRIGGER IF EXISTS tgr_player_update ON players;
CREATE OR REPLACE FUNCTION player_hist_change() RETURNS trigger AS $$ BEGIN INSERT INTO players_hist(ucid, discord_id, name, manual) SELECT OLD.ucid, OLD.discord_id, OLD.name, COALESCE(OLD.manual, FALSE); RETURN NEW; END; $$ LANGUAGE 'plpgsql';
CREATE TRIGGER tgr_player_update AFTER UPDATE OF discord_id, name, manual ON players FOR EACH ROW EXECUTE PROCEDURE player_hist_change();
UPDATE players SET manual = FALSE WHERE manual IS NULL;
ALTER TABLE players ALTER COLUMN manual SET NOT NULL;
