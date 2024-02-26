CREATE TABLE nodestats (id SERIAL PRIMARY KEY, node TEXT NOT NULL, pool_size INTEGER NOT NULL, pool_available INTEGER NOT NULL, requests_waiting INTEGER NOT NULL, requests_wait_ms INTEGER NOT NULL, workers INTEGER NOT NULL, qsize INTEGER NOT NULL, time TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'));
CREATE INDEX IF NOT EXISTS idx_nodestats_node ON nodestats(node);
CREATE INDEX IF NOT EXISTS idx_nodestats_time ON nodestats(time);
