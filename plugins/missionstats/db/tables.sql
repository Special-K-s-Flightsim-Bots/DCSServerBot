CREATE TABLE IF NOT EXISTS missionstats (
    id SERIAL PRIMARY KEY,
    mission_id INTEGER NOT NULL,
    event TEXT NOT NULL,
    init_id TEXT,
    init_side TEXT,
    init_type TEXT,
    init_cat TEXT,
    target_id TEXT,
    target_side TEXT,
    target_type TEXT,
    target_cat TEXT,
    weapon TEXT,
    place TEXT,
    comment TEXT,
    time TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    FOREIGN KEY (init_id) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (mission_id) REFERENCES missions (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_missionstats_init_id ON missionstats(init_id);
CREATE INDEX IF NOT EXISTS idx_missionstats_target_id ON missionstats(target_id);
CREATE INDEX IF NOT EXISTS idx_missionstats_event_init_id on missionstats (event, init_id);
