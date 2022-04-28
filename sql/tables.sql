CREATE TABLE IF NOT EXISTS version (version TEXT PRIMARY KEY);
INSERT INTO version (version) VALUES ('v1.5') ON CONFLICT (version) DO NOTHING;
CREATE TABLE IF NOT EXISTS plugins (plugin TEXT PRIMARY KEY, version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS servers (server_name TEXT PRIMARY KEY, agent_host TEXT NOT NULL, host TEXT NOT NULL DEFAULT '127.0.0.1', port BIGINT NOT NULL, blue_password TEXT, red_password TEXT);
CREATE TABLE IF NOT EXISTS message_persistence (server_name TEXT NOT NULL, embed_name TEXT NOT NULL, embed BIGINT NOT NULL, PRIMARY KEY (server_name, embed_name));
