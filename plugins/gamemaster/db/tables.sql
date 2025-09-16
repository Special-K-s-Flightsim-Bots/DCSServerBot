CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE TABLE IF NOT EXISTS campaigns (id SERIAL PRIMARY KEY, name TEXT NOT NULL, description TEXT, image_url TEXT, start TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'), stop TIMESTAMP);
CREATE TABLE IF NOT EXISTS campaigns_servers (campaign_id INTEGER NOT NULL, server_name TEXT NOT NULL, PRIMARY KEY (campaign_id, server_name));
ALTER TABLE campaigns ADD CONSTRAINT campaigns_prevent_double EXCLUDE USING gist(tsrange(start, stop, '[)') WITH &&);
ALTER TABLE campaigns ADD CONSTRAINT campaigns_check_start_before_stop CHECK (start < stop);
CREATE UNIQUE INDEX IF NOT EXISTS idx_campaigns_name ON campaigns (name);
CREATE TABLE IF NOT EXISTS coalitions (server_name TEXT NOT NULL, player_ucid TEXT NOT NULL, coalition TEXT, coalition_leave TIMESTAMP, PRIMARY KEY(server_name, player_ucid));
CREATE TABLE IF NOT EXISTS messages (id SERIAL PRIMARY KEY, sender TEXT NOT NULL, player_ucid TEXT NOT NULL, message TEXT NOT NULL, ack BOOLEAN NOT NULL DEFAULT TRUE, time TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'));
