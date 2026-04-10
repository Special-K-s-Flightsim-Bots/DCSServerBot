CREATE TABLE IF NOT EXISTS pu_events (
    id SERIAL PRIMARY KEY,
    init_id TEXT NOT NULL,
    target_id TEXT,
    server_name TEXT NOT NULL,
    event TEXT NOT NULL,
    points DECIMAL NOT NULL,
    time TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    decay_run INTEGER NOT NULL DEFAULT -1,
    FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (init_id) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_pu_events_init_id ON pu_events(init_id);
CREATE INDEX IF NOT EXISTS idx_pu_events_target_id ON pu_events(target_id);
CREATE UNIQUE INDEX idx_pu_events_unique ON pu_events (init_id, COALESCE(target_id, '-1'), event, DATE_TRUNC('minute', time));
