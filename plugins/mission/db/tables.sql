INSERT INTO plugins (plugin, version) VALUES ('mission', 'v1.2') ON CONFLICT (plugin) DO NOTHING;
CREATE TABLE IF NOT EXISTS players (ucid TEXT PRIMARY KEY, discord_id BIGINT, name TEXT, ipaddr TEXT, last_seen TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_players_discord_id ON players(discord_id);
CREATE TABLE IF NOT EXISTS missions (id SERIAL PRIMARY KEY, server_name TEXT NOT NULL, mission_name TEXT NOT NULL, mission_theatre TEXT NOT NULL, mission_start TIMESTAMP NOT NULL DEFAULT NOW(), mission_end TIMESTAMP);
