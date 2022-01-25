INSERT INTO plugins (plugin, version) VALUES ('admin', 'v1.0') ON CONFLICT (plugin) DO NOTHING;
CREATE TABLE IF NOT EXISTS bans (ucid TEXT PRIMARY KEY, banned_by TEXT NOT NULL, reason TEXT, banned_at TIMESTAMP NOT NULL DEFAULT NOW());
