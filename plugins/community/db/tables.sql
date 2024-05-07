CREATE TABLE IF NOT EXISTS all_servers(id SERIAL PRIMARY KEY, server_name TEXT NOT NULL, address TEXT NOT NULL, port INTEGER NOT NULL, num_players INTEGER NOT NULL, max_players INTEGER NOT NULL, geocontinent TEXT NOT NULL, geocountry TEXT NOT NULL, time TIMESTAMP DEFAULT (now() at time zone 'utc'));
CREATE INDEX IF NOT EXISTS idx_all_servers_name ON all_servers(server_name);
CREATE INDEX IF NOT EXISTS idx_all_servers_continent ON all_servers(geocontinent);
CREATE INDEX IF NOT EXISTS idx_all_servers_country ON all_servers(geocountry);
