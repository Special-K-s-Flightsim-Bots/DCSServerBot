ALTER TABLE intercom ADD COLUMN IF NOT EXISTS guild_id BIGINT NOT NULL;
ALTER TABLE intercom DROP COLUMN IF EXISTS priority;
ALTER TABLE files ADD COLUMN IF NOT EXISTS guild_id BIGINT NOT NULL;
CREATE TABLE IF NOT EXISTS broadcasts (id SERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, node TEXT NOT NULL, time TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'), data JSON);
CREATE OR REPLACE FUNCTION intercom_notify() RETURNS trigger AS $$ BEGIN PERFORM pg_notify('intercom', NEW.node); RETURN NEW; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER intercom_trigger AFTER INSERT OR UPDATE ON intercom FOR EACH ROW EXECUTE PROCEDURE intercom_notify();
CREATE OR REPLACE FUNCTION broadcasts_notify() RETURNS trigger AS $$ BEGIN PERFORM pg_notify('broadcasts', NEW.node); RETURN NEW; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER broadcasts_trigger AFTER INSERT OR UPDATE ON broadcasts FOR EACH ROW EXECUTE PROCEDURE broadcasts_notify();
UPDATE version SET version='v3.10';
