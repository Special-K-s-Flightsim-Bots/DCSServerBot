CREATE TABLE IF NOT EXISTS cluster (
    guild_id BIGINT PRIMARY KEY,
    guild_name TEXT,
    master TEXT NOT NULL,
    takeover_requested_by TEXT NULL,
    version TEXT NOT NULL,
    update_pending BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE IF NOT EXISTS nodes (
    guild_id BIGINT NOT NULL,
    node TEXT NOT NULL,
    last_seen TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    ready BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (guild_id, node)
);
CREATE TABLE IF NOT EXISTS files (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    data BYTEA NOT NULL,
    created TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);
CREATE TABLE IF NOT EXISTS resources (
    id          TEXT PRIMARY KEY,          -- sha256(type|machine|normalized path); derived, never stored
    type        TEXT NOT NULL,             -- 'dcs_installation' today
    scope       TEXT NOT NULL,             -- declared BY TYPE: what a window on it is allowed to stop
    owner_guild BIGINT,                    -- first declarer; reporting only, carries no veto
    path        TEXT NOT NULL,
    created     TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);
CREATE TABLE IF NOT EXISTS resource_members (
    resource_id TEXT NOT NULL,             -- a machine several clusters share, one row per cluster and node
    guild_id    BIGINT NOT NULL,
    node        TEXT NOT NULL,
    servers_up  INT NOT NULL DEFAULT 0,    -- published by this node only; 0 means nothing of its is running
    changed     TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    PRIMARY KEY (resource_id, guild_id, node)
);
CREATE TABLE IF NOT EXISTS resource_dependents (
    resource_id TEXT NOT NULL,             -- what has to stop when the resource goes down
    guild_id    BIGINT NOT NULL,
    node        TEXT NOT NULL,
    kind        TEXT NOT NULL,             -- 'server' is the only kind so far
    name        TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'in_service',   -- in_service | stepped_down
    PRIMARY KEY (resource_id, guild_id, node, kind, name)
);
CREATE TABLE IF NOT EXISTS resource_window (
    resource_id  TEXT PRIMARY KEY,         -- a row here means the resource is currently TAKEN
    holder_guild BIGINT NOT NULL,
    holder_node  TEXT NOT NULL,
    action       TEXT NOT NULL,            -- 'update' | 'repair' | 'module'
    scope        TEXT NOT NULL,            -- copied from resources.scope: what this window may stop
    started      TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);
CREATE TABLE IF NOT EXISTS resource_ack (
    resource_id TEXT NOT NULL,             -- a member's confirmation that it has stepped down
    guild_id    BIGINT NOT NULL,
    node        TEXT NOT NULL,
    acked       TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    PRIMARY KEY (resource_id, guild_id, node)
);
CREATE OR REPLACE FUNCTION federation_window_notify() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('federation_window', NEW.resource_id);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'resource_window_trigger' AND tgrelid = 'resource_window'::regclass
    ) THEN
        CREATE TRIGGER resource_window_trigger
        AFTER INSERT OR UPDATE ON resource_window
        FOR EACH ROW EXECUTE PROCEDURE federation_window_notify();
    END IF;
END;
$$;
