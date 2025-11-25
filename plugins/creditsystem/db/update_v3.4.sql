DELETE FROM credits WHERE player_ucid NOT IN (SELECT ucid FROM players);
DELETE FROM credits WHERE campaign_id NOT IN (SELECT id FROM campaigns);
ALTER TABLE credits ADD CONSTRAINT credits_player_ucid_fkey FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE;
ALTER TABLE credits ADD CONSTRAINT credits_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES campaigns (id) ON DELETE CASCADE;
DELETE FROM credits_log WHERE player_ucid NOT IN (SELECT ucid FROM players);
DELETE FROM credits_log WHERE campaign_id NOT IN (SELECT id FROM campaigns);
ALTER TABLE credits_log ADD CONSTRAINT credits_log_player_ucid_fkey FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE;
ALTER TABLE credits_log ADD CONSTRAINT credits_log_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES campaigns (id) ON DELETE CASCADE;
DELETE FROM squadron_credits WHERE squadron_id NOT IN (SELECT id FROM squadrons);
DELETE FROM squadron_credits WHERE campaign_id NOT IN (SELECT id FROM campaigns);
ALTER TABLE squadron_credits ADD CONSTRAINT squadron_credits_squadron_id_fkey FOREIGN KEY (squadron_id) REFERENCES squadrons (id) ON DELETE CASCADE;
ALTER TABLE squadron_credits ADD CONSTRAINT squadron_credits_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES campaigns (id) ON DELETE CASCADE;
DELETE FROM squadron_credits_log WHERE squadron_id NOT IN (SELECT id FROM squadrons);
DELETE FROM squadron_credits_log WHERE campaign_id NOT IN (SELECT id FROM campaigns);
ALTER TABLE squadron_credits_log ADD CONSTRAINT squadron_credits_log_squadron_id_fkey FOREIGN KEY (squadron_id) REFERENCES squadrons (id) ON DELETE CASCADE;
ALTER TABLE squadron_credits_log ADD CONSTRAINT squadron_credits_log_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES campaigns (id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_credit_log_ucid ON credits_log (campaign_id, player_ucid);
CREATE INDEX IF NOT EXISTS idx_squadron_credits_log ON squadron_credits_log (campaign_id, squadron_id, player_ucid);
CREATE TABLE IF NOT EXISTS players_badges (
    campaign_id INTEGER NOT NULL,
    player_ucid TEXT NOT NULL,
    badge_name TEXT NOT NULL,
    badge_url TEXT,
    time TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    PRIMARY KEY (campaign_id, player_ucid),
    FOREIGN KEY (player_ucid) REFERENCES players(ucid) ON DELETE CASCADE,
    FOREIGN KEY (campaign_id) REFERENCES campaigns (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_players_badges ON players_badges (player_ucid);
