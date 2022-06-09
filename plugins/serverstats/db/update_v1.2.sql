UPDATE plugins SET version = 'v1.3' WHERE plugin = 'serverstats';
CREATE INDEX IF NOT EXISTS idx_serverstats_server_time ON serverstats(time);
