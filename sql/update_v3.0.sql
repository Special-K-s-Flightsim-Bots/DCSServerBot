ALTER TABLE servers RENAME TO servers_old;
CREATE TABLE IF NOT EXISTS instances (instance TEXT PRIMARY KEY, node TEXT NOT NULL, port BIGINT NOT NULL, server_name TEXT, last_seen TIMESTAMP DEFAULT NOW());
CREATE UNIQUE INDEX IF NOT EXISTS idx_instances ON instances (node, port);
CREATE UNIQUE INDEX IF NOT EXISTS idx_instances_server_name ON instances (server_name);
CREATE TABLE IF NOT EXISTS servers (server_name TEXT PRIMARY KEY, blue_password TEXT, red_password TEXT);
INSERT INTO servers (SELECT server_name, blue_password, red_password FROM servers_old);
DROP TABLE servers_old;
UPDATE version SET version='v3.1';
