ALTER TABLE trueskill ADD COLUMN IF NOT EXISTS time TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC');
CREATE TABLE IF NOT EXISTS trueskill_hist (player_ucid TEXT, skill_mu NUMERIC NOT NULL, skill_sigma NUMERIC NOT NULL, time TIMESTAMP, PRIMARY KEY (player_ucid, time));
CREATE OR REPLACE FUNCTION trueskill_hist_change()
RETURNS trigger
AS $$
BEGIN
    INSERT INTO trueskill_hist(player_ucid, skill_mu, skill_sigma, time)
    SELECT OLD.player_ucid, OLD.skill_mu, OLD.skill_sigma, old.time;
    RETURN NEW;
END;
$$ LANGUAGE 'plpgsql';
CREATE TRIGGER tgr_trueskill_update
    AFTER UPDATE OF skill_mu, skill_sigma ON trueskill
    FOR EACH ROW
    EXECUTE PROCEDURE trueskill_hist_change();
