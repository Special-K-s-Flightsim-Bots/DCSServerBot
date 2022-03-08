UPDATE plugins SET version = 'v1.2' WHERE plugin = 'serverstats';
CREATE INDEX IF NOT EXISTS idx_serverstats_server_name ON serverstats(server_name);
