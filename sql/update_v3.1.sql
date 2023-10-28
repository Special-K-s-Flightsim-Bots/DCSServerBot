ALTER TABLE instances RENAME TO instances_old;
CREATE TABLE IF NOT EXISTS instances (node TEXT NOT NULL, instance TEXT NOT NULL, port BIGINT NOT NULL, server_name TEXT, last_seen TIMESTAMP DEFAULT NOW(), PRIMARY KEY(node, instance));
CREATE UNIQUE INDEX IF NOT EXISTS idx_instances ON instances (node, port);
CREATE UNIQUE INDEX IF NOT EXISTS idx_instances_server_name ON instances (server_name);
INSERT INTO instances SELECT node, instance, port, server_name, last_seen FROM instances_old;
DROP TABLE instances_old;
UPDATE version SET version='v3.2';
