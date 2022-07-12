CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE TABLE IF NOT EXISTS campaigns (id SERIAL PRIMARY KEY, name TEXT NOT NULL, description TEXT, start TIMESTAMP NOT NULL DEFAULT NOW(), stop TIMESTAMP);
CREATE TABLE IF NOT EXISTS campaigns_servers (campaign_id INTEGER NOT NULL, server_name TEXT NOT NULL, PRIMARY KEY (campaign_id, server_name));
ALTER TABLE campaigns ADD CONSTRAINT campaigns_prevent_double EXCLUDE USING gist(tsrange(start, stop, '[)') WITH &&);
ALTER TABLE campaigns ADD CONSTRAINT campaigns_check_start_before_stop CHECK (start < stop);
CREATE UNIQUE INDEX IF NOT EXISTS idx_campaigns_name ON campaigns (name);
