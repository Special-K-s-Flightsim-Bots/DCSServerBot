CREATE TABLE IF NOT EXISTS credits (
    campaign_id INTEGER NOT NULL,
    player_ucid TEXT NOT NULL,
    points INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (campaign_id, player_ucid),
    FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (campaign_id) REFERENCES campaigns (id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS credits_log (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER NOT NULL,
    event TEXT NOT NULL,
    player_ucid TEXT NOT NULL,
    old_points INTEGER NOT NULL,
    new_points INTEGER NOT NULL,
    remark TEXT,
    time TIMESTAMP DEFAULT (now() AT TIME ZONE 'utc'),
    FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (campaign_id) REFERENCES campaigns (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_credit_log_ucid ON credits_log (campaign_id, player_ucid);
CREATE TABLE IF NOT EXISTS squadron_credits (
    campaign_id INTEGER NOT NULL,
    squadron_id INTEGER NOT NULL,
    points INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (campaign_id, squadron_id),
    FOREIGN KEY (squadron_id) REFERENCES squadrons (id) ON DELETE CASCADE,
    FOREIGN KEY (campaign_id) REFERENCES campaigns (id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS squadron_credits_log (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER NOT NULL,
    event TEXT NOT NULL,
    squadron_id INTEGER NOT NULL,
    old_points INTEGER NOT NULL,
    new_points INTEGER NOT NULL,
    player_ucid TEXT,
    remark TEXT,
    time TIMESTAMP DEFAULT (now() AT TIME ZONE 'utc'),
    FOREIGN KEY (squadron_id) REFERENCES squadrons (id) ON DELETE CASCADE,
    FOREIGN KEY (campaign_id) REFERENCES campaigns (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_squadron_credits_log ON squadron_credits_log (campaign_id, squadron_id, player_ucid);
CREATE TABLE IF NOT EXISTS players_badges (
    campaign_id INTEGER NOT NULL,
    player_ucid TEXT NOT NULL,
    badge_name TEXT NOT NULL,
    badge_url TEXT,
    time TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    PRIMARY KEY (campaign_id, player_ucid),
    FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON DELETE CASCADE,
    FOREIGN KEY (campaign_id) REFERENCES campaigns (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_players_badges ON players_badges (player_ucid);
