INSERT INTO plugins (plugin, version) VALUES ('punishment', 'v1.0') ON CONFLICT (plugin) DO NOTHING;
CREATE TABLE IF NOT EXISTS pu_events (id SERIAL PRIMARY KEY, init_id TEXT NOT NULL, target_id TEXT, event TEXT NOT NULL, points DECIMAL NOT NULL, time TIMESTAMP NOT NULL DEFAULT NOW());
CREATE INDEX IF NOT EXISTS idx_pu_events_init_id ON pu_events(init_id);
CREATE INDEX IF NOT EXISTS idx_pu_events_target_id ON pu_events(target_id);
