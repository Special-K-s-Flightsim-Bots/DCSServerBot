CREATE TABLE IF NOT EXISTS trueskill (
    player_ucid TEXT PRIMARY KEY,
    skill_mu NUMERIC NOT NULL,
    skill_sigma NUMERIC NOT NULL,
    time TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS trueskill_hist (
    player_ucid TEXT,
    skill_mu NUMERIC NOT NULL,
    skill_sigma NUMERIC NOT NULL,
    time TIMESTAMP, PRIMARY KEY (player_ucid, time),
    FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE OR REPLACE FUNCTION trueskill_hist_change()
RETURNS trigger
AS $$
BEGIN
    INSERT INTO trueskill_hist (player_ucid, skill_mu, skill_sigma, time)
    VALUES (OLD.player_ucid, OLD.skill_mu, OLD.skill_sigma, OLD.time)
    ON CONFLICT (player_ucid, time) DO UPDATE
    SET skill_mu    = EXCLUDED.skill_mu,
        skill_sigma = EXCLUDED.skill_sigma;

    RETURN NEW;
END;
$$ LANGUAGE 'plpgsql';
CREATE TRIGGER tgr_trueskill_update
    AFTER UPDATE OF skill_mu, skill_sigma ON trueskill
    FOR EACH ROW
    EXECUTE PROCEDURE trueskill_hist_change();
