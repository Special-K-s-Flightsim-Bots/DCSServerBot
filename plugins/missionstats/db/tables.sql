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
    weapon_id INTEGER,
    weapon TEXT,
    place TEXT,
    comment TEXT,
    time TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    FOREIGN KEY (init_id) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (mission_id) REFERENCES missions (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_missionstats_mission_id ON missionstats(mission_id);
CREATE INDEX IF NOT EXISTS idx_missionstats_init_id ON missionstats(init_id);
CREATE INDEX IF NOT EXISTS idx_missionstats_target_id ON missionstats(target_id);
CREATE INDEX IF NOT EXISTS idx_missionstats_event_init_id on missionstats (event, init_id);
CREATE INDEX IF NOT EXISTS idx_missionstats_init_id_time on missionstats (init_id, time);
CREATE INDEX IF NOT EXISTS idx_missionstats_target_id_time on missionstats (target_id, time);
CREATE TABLE IF NOT EXISTS refuelingstats (
    id SERIAL PRIMARY KEY,
    mission_id INTEGER NOT NULL,
    init_id TEXT NOT NULL,
    init_type TEXT NOT NULL,
    tanker TEXT NOT NULL,
    fuel_taken INTEGER,
    transfer_complete BOOLEAN,
    transfer_time DECIMAL,
    time TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);
CREATE INDEX IF NOT EXISTS idx_refuelingstats_ucid ON refuelingstats(init_id);
CREATE INDEX IF NOT EXISTS idx_refuelingstats_init_id_time on refuelingstats(init_id, time);
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_site_air_starts AS
SELECT m.server_name,
       mst.init_id AS player_ucid,
       DATE_TRUNC('week', mst.time)::date AS week_start,
       COUNT(*)::bigint AS starts
  FROM missionstats mst
  JOIN missions m ON m.id = mst.mission_id
 WHERE (mst.event = 'S_EVENT_TAKEOFF' OR (mst.event = 'S_EVENT_BIRTH' AND COALESCE(mst.place, '') = ''))
 GROUP BY 1, 2, 3;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_site_air_starts_key ON mv_site_air_starts (server_name, player_ucid, week_start);
CREATE INDEX IF NOT EXISTS idx_mv_site_air_starts_ucid ON mv_site_air_starts (player_ucid);
