CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE TABLE IF NOT EXISTS campaigns (campaign_id SERIAL PRIMARY KEY, server_name TEXT, mission_name TEXT);
CREATE TABLE IF NOT EXISTS campaigns2 (id SERIAL PRIMARY KEY, name TEXT NOT NULL, description TEXT, server_name TEXT NOT NULL, start TIMESTAMP NOT NULL DEFAULT NOW(), stop TIMESTAMP);
CREATE UNIQUE INDEX IF NOT EXISTS idx_campaigns_key on campaigns2 (server_name, name);
INSERT INTO campaigns2 (id, name, server_name, start) SELECT campaign_id, 'Initial', server_name, TO_DATE('01.01.1900', 'DD.MM.YYYY') FROM campaigns c WHERE campaign_id = (select max(campaign_id) FROM campaigns WHERE server_name = c.server_name);
DROP TABLE IF EXISTS campaigns;
ALTER TABLE campaigns2 RENAME TO campaigns;
ALTER TABLE campaigns ADD CONSTRAINT campaigns_prevent_double EXCLUDE USING gist(server_name WITH =, tsrange(start, stop, '[)') WITH &&);
ALTER TABLE campaigns ADD CONSTRAINT campaigns_check_start_before_stop CHECK (start < stop);
