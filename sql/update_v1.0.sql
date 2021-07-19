CREATE TABLE IF NOT EXISTS version (version TEXT PRIMARY KEY);
INSERT INTO version (version) VALUES ('v1.0') ON CONFLICT (version) DO NOTHING;
CREATE TABLE IF NOT EXISTS message_persistence (server_name TEXT NOT NULL, embed_name TEXT NOT NULL, embed BIGINT NOT NULL, PRIMARY KEY (server_name, embed_name));
INSERT INTO message_persistence SELECT server_name, 'players_embed', players_embed FROM servers UNION SELECT server_name, 'mission_embed', mission_embed FROM servers;
CREATE TABLE servers_tmp (server_name TEXT PRIMARY KEY, agent_host TEXT NOT NULL, host TEXT NOT NULL DEFAULT '127.0.0.1', port BIGINT NOT NULL, chat_channel BIGINT, status_channel BIGINT, admin_channel BIGINT);
INSERT INTO servers_tmp SELECT server_name, agent_host, host, port, chat_channel, status_channel, admin_channel FROM servers;
DROP TABLE servers;
ALTER TABLE servers_tmp RENAME TO servers;
UPDATE version SET version='v1.1';
