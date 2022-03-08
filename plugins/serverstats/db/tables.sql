INSERT INTO plugins (plugin, version) VALUES ('serverstats', 'v1.2') ON CONFLICT (plugin) DO NOTHING;
CREATE TABLE serverstats (id SERIAL PRIMARY KEY, agent_host TEXT NOT NULL, server_name TEXT NOT NULL, mission_id INTEGER NOT NULL, users INTEGER NOT NULL, status TEXT NOT NULL, cpu NUMERIC(5,2) NOT NULL, mem_total NUMERIC NOT NULL, mem_ram NUMERIC NOT NULL, read_bytes NUMERIC NOT NULL, write_bytes NUMERIC NOT NULL, bytes_sent NUMERIC NOT NULL, bytes_recv NUMERIC NOT NULL, fps NUMERIC(5,2) NOT NULL, time TIMESTAMP NOT NULL DEFAULT NOW());
CREATE INDEX IF NOT EXISTS idx_serverstats_server_name ON serverstats(server_name);
