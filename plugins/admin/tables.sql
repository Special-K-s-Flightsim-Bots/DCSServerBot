INSERT INTO plugins (plugin, version) VALUES ('admin', 'v1.0') ON CONFLICT (plugin) DO NOTHING;
CREATE TABLE IF NOT EXISTS servers (server_name TEXT PRIMARY KEY, agent_host TEXT NOT NULL, host TEXT NOT NULL DEFAULT '127.0.0.1', port BIGINT NOT NULL, chat_channel BIGINT, status_channel BIGINT, admin_channel BIGINT);
CREATE TABLE IF NOT EXISTS message_persistence (server_name TEXT NOT NULL, embed_name TEXT NOT NULL, embed BIGINT NOT NULL, PRIMARY KEY (server_name, embed_name));
CREATE TABLE IF NOT EXISTS bans (ucid TEXT PRIMARY KEY, banned_by TEXT NOT NULL, reason TEXT, banned_at TIMESTAMP NOT NULL DEFAULT NOW());
