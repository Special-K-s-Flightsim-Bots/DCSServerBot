INSERT INTO plugins (plugin, version) VALUES ('slotblocking', 'v1.0') ON CONFLICT (plugin) DO NOTHING;
CREATE TABLE IF NOT EXISTS campaigns (campaign_id SERIAL PRIMARY KEY, server_name TEXT, mission_name TEXT);
CREATE TABLE IF NOT EXISTS sb_points (campaign_id INTEGER NOT NULL, player_ucid TEXT NOT NULL, points INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(campaign_id, player_ucid));
