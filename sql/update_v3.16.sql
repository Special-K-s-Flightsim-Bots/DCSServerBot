DELETE FROM message_persistence WHERE server_name != 'Master' AND server_name NOT IN (SELECT server_name FROM servers);
CREATE TABLE IF NOT EXISTS message_persistence_new (
    id SERIAL PRIMARY KEY,
    server_name TEXT,
    embed_name TEXT NOT NULL,
    embed BIGINT NOT NULL,
    thread BIGINT NULL,
    FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE
);
ALTER TABLE message_persistence_new ADD COLUMN server_name_norm text GENERATED ALWAYS AS (COALESCE(server_name, '')) STORED;
ALTER TABLE message_persistence_new ADD CONSTRAINT uq_message_persistence_norm UNIQUE (server_name_norm, embed_name);
INSERT INTO message_persistence_new (server_name, embed_name, embed, thread) SELECT server_name, embed_name, embed, thread FROM message_persistence WHERE server_name != 'Master';
INSERT INTO message_persistence_new (server_name, embed_name, embed, thread) SELECT NULL, embed_name, embed, thread FROM message_persistence WHERE server_name = 'Master';
DROP TABLE message_persistence;
ALTER TABLE message_persistence_new RENAME TO message_persistence;
DELETE FROM instances WHERE server_name NOT IN (SELECT server_name FROM servers);
ALTER TABLE instances ADD CONSTRAINT instances_server_name_fkey FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE;
DELETE FROM audit WHERE server_name IS NOT NULL AND server_name NOT IN (SELECT server_name FROM servers);
ALTER TABLE audit ADD CONSTRAINT audit_server_name_fkey FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE;
UPDATE version SET version='v3.17';
