CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE TABLE IF NOT EXISTS campaigns (id SERIAL PRIMARY KEY, name TEXT NOT NULL, description TEXT, server_name TEXT NOT NULL, start TIMESTAMP NOT NULL DEFAULT NOW(), stop TIMESTAMP);
CREATE UNIQUE INDEX IF NOT EXISTS idx_campaigns_key on campaigns (server_name, name);
ALTER TABLE campaigns ADD CONSTRAINT campaigns_prevent_double EXCLUDE USING gist(server_name WITH =, tsrange(start, stop, '[)') WITH &&);
ALTER TABLE campaigns ADD CONSTRAINT campaigns_check_start_before_stop CHECK (start < stop);
