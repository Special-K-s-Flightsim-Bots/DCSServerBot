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
    PRIMARY KEY (guild_id, node)
);
CREATE TABLE IF NOT EXISTS files (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    data BYTEA NOT NULL,
    created TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);
