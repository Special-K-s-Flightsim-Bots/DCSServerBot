DROP TABLE IF EXISTS squadron_credits;
DELETE FROM squadron_credits_log;
CREATE TABLE IF NOT EXISTS squadron_credits (campaign_id INTEGER NOT NULL, squadron_id INTEGER NOT NULL, points INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(campaign_id, squadron_id));
