INSERT INTO plugins (plugin, version) VALUES ('slotblocking', 'v1.2') ON CONFLICT (plugin) DO NOTHING;
CREATE TABLE IF NOT EXISTS credits (campaign_id INTEGER NOT NULL, player_ucid TEXT NOT NULL, points INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(campaign_id, player_ucid));
