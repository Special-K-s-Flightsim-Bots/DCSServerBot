ALTER TABLE servers DROP COLUMN host;
ALTER TABLE servers RENAME COLUMN agent_host TO node;
DELETE FROM servers WHERE (node, port, last_seen) NOT IN (SELECT node, port, MAX(last_seen) AS "last_seen" FROM servers GROUP BY node, port);
CREATE UNIQUE INDEX IF NOT EXISTS idx_servers ON servers (node, port);
CREATE TABLE IF NOT EXISTS intercom (id SERIAL PRIMARY KEY, node TEXT NOT NULL, time TIMESTAMP NOT NULL DEFAULT NOW(), data JSON);
CREATE TABLE IF NOT EXISTS nodes (guild_id BIGINT NOT NULL, node TEXT NOT NULL, master BOOLEAN NOT NULL, last_seen TIMESTAMP DEFAULT NOW(), PRIMARY KEY (guild_id, node));
CREATE TABLE IF NOT EXISTS files (id SERIAL PRIMARY KEY, name TEXT NOT NULL, data BYTEA NOT NULL, created TIMESTAMP NOT NULL DEFAULT NOW());
UPDATE version SET version='v3.0';
