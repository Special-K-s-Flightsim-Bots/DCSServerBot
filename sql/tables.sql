CREATE TABLE IF NOT EXISTS version (version TEXT PRIMARY KEY);
INSERT INTO version (version) VALUES ('v3.12') ON CONFLICT (version) DO NOTHING;
CREATE TABLE IF NOT EXISTS cluster (guild_id BIGINT primary key, master TEXT NOT NULL, version TEXT NOT NULL, UPDATE_PENDING BOOLEAN NOT NULL DEFAULT FALSE);
CREATE TABLE IF NOT EXISTS plugins (plugin TEXT PRIMARY KEY, version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS message_persistence (server_name TEXT NOT NULL, embed_name TEXT NOT NULL, embed BIGINT NOT NULL, thread BIGINT NULL, PRIMARY KEY (server_name, embed_name));
CREATE TABLE IF NOT EXISTS nodes (guild_id BIGINT NOT NULL, node TEXT NOT NULL, last_seen TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'), PRIMARY KEY (guild_id, node));
CREATE TABLE IF NOT EXISTS instances (node TEXT NOT NULL, instance TEXT NOT NULL, port BIGINT NOT NULL, server_name TEXT, last_seen TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'), PRIMARY KEY(node, instance));
CREATE UNIQUE INDEX IF NOT EXISTS idx_instances ON instances (node, port);
CREATE UNIQUE INDEX IF NOT EXISTS idx_instances_server_name ON instances (server_name);
CREATE TABLE IF NOT EXISTS servers (server_name TEXT PRIMARY KEY, blue_password TEXT, red_password TEXT, maintenance BOOLEAN NOT NULL DEFAULT FALSE);
CREATE TABLE IF NOT EXISTS intercom (id SERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, node TEXT NOT NULL, time TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'), data JSON);
CREATE INDEX IF NOT EXISTS idx_intercom_node ON intercom (node);
CREATE TABLE IF NOT EXISTS files (id SERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, name TEXT NOT NULL, data BYTEA NOT NULL, created TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'));
CREATE TABLE IF NOT EXISTS audit(id SERIAL PRIMARY KEY, node TEXT NOT NULL, event TEXT NOT NULL, server_name TEXT, discord_id BIGINT, ucid TEXT, time TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'));
CREATE TABLE IF NOT EXISTS broadcasts (id SERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, node TEXT NOT NULL, time TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'), data JSON);
CREATE OR REPLACE FUNCTION intercom_notify()
RETURNS trigger
AS $$
BEGIN
    PERFORM pg_notify('intercom', NEW.node);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER intercom_trigger AFTER INSERT OR UPDATE ON intercom FOR EACH ROW EXECUTE PROCEDURE intercom_notify();
CREATE OR REPLACE FUNCTION broadcasts_notify()
RETURNS trigger
AS $$
BEGIN
    PERFORM pg_notify('broadcasts', NEW.node);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER broadcasts_trigger AFTER INSERT OR UPDATE ON broadcasts FOR EACH ROW EXECUTE PROCEDURE broadcasts_notify();
