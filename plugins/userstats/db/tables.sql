CREATE TABLE IF NOT EXISTS statistics (
    mission_id INTEGER NOT NULL,
    player_ucid TEXT NOT NULL,
    slot TEXT NOT NULL,
    tail_no TEXT NULL,
    side INTEGER DEFAULT 0,
    kills INTEGER DEFAULT 0,
    pvp INTEGER DEFAULT 0,
    deaths INTEGER DEFAULT 0,
    ejections INTEGER DEFAULT 0,
    crashes INTEGER DEFAULT 0,
    teamkills INTEGER DEFAULT 0,
    kills_planes INTEGER DEFAULT 0,
    kills_helicopters INTEGER DEFAULT 0,
    kills_ships INTEGER DEFAULT 0,
    kills_sams INTEGER DEFAULT 0,
    kills_ground INTEGER DEFAULT 0,
    deaths_pvp INTEGER DEFAULT 0,
    deaths_planes INTEGER DEFAULT 0,
    deaths_helicopters INTEGER DEFAULT 0,
    deaths_ships INTEGER DEFAULT 0,
    deaths_sams INTEGER DEFAULT 0,
    deaths_ground INTEGER DEFAULT 0,
    takeoffs INTEGER DEFAULT 0,
    landings INTEGER DEFAULT 0,
    hop_on TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    hop_off TIMESTAMP,
    PRIMARY KEY (mission_id, player_ucid, slot, hop_on),
    FOREIGN KEY (mission_id) REFERENCES missions (id) ON DELETE CASCADE,
    FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_statistics_player_ucid ON statistics(player_ucid);
CREATE INDEX IF NOT EXISTS idx_statistics_mission_id ON statistics(mission_id);
CREATE INDEX IF NOT EXISTS idx_statistics_hop_on ON statistics(hop_on);
CREATE TABLE IF NOT EXISTS squadrons (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NULL,
    role BIGINT NULL,
    co_ucid TEXT NULL REFERENCES players(ucid) ON UPDATE CASCADE ON DELETE SET NULL,
    xo_ucid TEXT NULL REFERENCES players(ucid) ON UPDATE CASCADE ON DELETE SET NULL,
    image_url TEXT NULL,
    channel BIGINT NULL,
    locked BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_squadrons_name ON squadrons (name);
CREATE TABLE IF NOT EXISTS squadron_members (
    squadron_id INTEGER NOT NULL,
    player_ucid TEXT NOT NULL,
    rank TEXT NULL,
    position TEXT NULL,
    joined_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    PRIMARY KEY (squadron_id, player_ucid),
    FOREIGN KEY (squadron_id) REFERENCES squadrons (id) ON DELETE CASCADE,
    FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_squadron_members ON squadron_members (player_ucid);
CREATE OR REPLACE FUNCTION clear_co_xo_on_member_delete()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_TABLE_NAME = 'squadron_members' AND TG_OP = 'DELETE' THEN
    UPDATE squadrons
       SET co_ucid = NULL
     WHERE id = OLD.squadron_id
       AND co_ucid = OLD.player_ucid;

    UPDATE squadrons
       SET xo_ucid = NULL
     WHERE id = OLD.squadron_id
       AND xo_ucid = OLD.player_ucid;
  END IF;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_clear_co_xo AFTER DELETE ON squadron_members FOR EACH ROW EXECUTE FUNCTION clear_co_xo_on_member_delete();
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_statistics AS
    SELECT s.player_ucid, m.server_name, s.slot, s.tail_no, s.side, COUNT(s.slot) AS usage, SUM(s.kills) AS kills,
           SUM(s.pvp) AS pvp, SUM(s.deaths) AS deaths, SUM(s.ejections) AS ejections, SUM(s.crashes) AS crashes,
           SUM(s.teamkills) AS teamkills, SUM(s.takeoffs) AS takeoffs, SUM(s.landings) AS landings,
           SUM(s.kills_planes) AS kills_planes, SUM(s.kills_helicopters) AS kills_helicopters,
           SUM(s.kills_ships) AS kills_ships, SUM(s.kills_sams) AS kills_sams, SUM(s.kills_ground) AS kills_ground,
           SUM(s.deaths_pvp) AS deaths_pvp, SUM(s.deaths_planes) AS deaths_planes,
           SUM(s.deaths_helicopters) AS deaths_helicopters, SUM(s.deaths_ships) AS deaths_ships,
           SUM(s.deaths_sams) AS deaths_sams, SUM(s.deaths_ground) AS deaths_ground,
           ROUND(SUM(EXTRACT(EPOCH FROM(COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on)))) AS playtime
    FROM statistics s JOIN missions m ON s.mission_id = m.id
    GROUP BY 1, 2, 3, 4, 5;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_statistics ON mv_statistics (player_ucid, server_name, slot, side, tail_no);
CREATE INDEX IF NOT EXISTS idx_mv_statistics_ucid ON mv_statistics (player_ucid);
CREATE INDEX IF NOT EXISTS idx_mv_statistics_tail_no ON mv_statistics (tail_no);
CREATE INDEX IF NOT EXISTS idx_mv_statistics_server ON mv_statistics (server_name);
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_serverstats AS
SELECT s.server_name, COUNT(DISTINCT p.ucid) AS "totalPlayers",
       (SUM(s.playtime) / 3600)::INTEGER AS "totalPlaytime",
       (SUM(s.playtime) / SUM(s.usage))::INTEGER AS "avgPlaytime",
       SUM(s.usage) AS "totalSorties",
       SUM(s.kills) AS "totalKills",
       SUM(s.deaths) AS "totalDeaths",
       SUM(s.pvp) AS "totalPvPKills",
       SUM(s.deaths_pvp) AS "totalPvPDeaths",
       NOW() AT TIME ZONE 'UTC' as "timestamp"
FROM players p
JOIN mv_statistics s ON p.ucid = s.player_ucid
GROUP BY 1;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_serverstats ON mv_serverstats (server_name);
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_site_activity AS
    SELECT m.server_name,
           DATE_TRUNC('week', s.hop_on)::date AS week_start,
           COALESCE(SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on))), 0)::bigint AS playtime,
           COUNT(DISTINCT s.player_ucid) AS pilots,
           COALESCE(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) FILTER (WHERE s.hop_off IS NOT NULL), 0) AS closed_playtime,
           COUNT(*) FILTER (WHERE s.hop_off IS NOT NULL) AS closed_sessions,
           COALESCE(SUM(s.takeoffs), 0)::bigint AS takeoffs
      FROM statistics s JOIN missions m ON s.mission_id = m.id
     GROUP BY 1, 2;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_site_activity ON mv_site_activity (server_name, week_start);
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_site_pilots AS
    SELECT m.server_name,
           s.player_ucid,
           MAX(DATE_TRUNC('week', s.hop_on)::date) AS last_week,
           (ARRAY_AGG(DISTINCT DATE_TRUNC('week', s.hop_on)::date
                      ORDER BY DATE_TRUNC('week', s.hop_on)::date DESC))[2] AS prev_week
      FROM statistics s JOIN missions m ON s.mission_id = m.id
     GROUP BY 1, 2;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_site_pilots ON mv_site_pilots (server_name, player_ucid);
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_site_pilot_totals AS
    SELECT g.server_name,
           g.player_ucid,
           SUM(g.playtime) AS playtime,
           SUM(g.sorties) AS sorties,
           SUM(g.takeoffs) AS takeoffs,
           SUM(g.landings) AS landings,
           SUM(g.kills) AS kills,
           SUM(g.pvp) AS pvp,
           SUM(g.crashes) AS crashes,
           SUM(g.ejections) AS ejections,
           SUM(g.kills_planes) AS kills_planes,
           SUM(g.kills_helicopters) AS kills_helicopters,
           SUM(g.kills_ships) AS kills_ships,
           SUM(g.kills_sams) AS kills_sams,
           SUM(g.kills_ground) AS kills_ground,
           SUM(g.deaths_pvp) AS deaths_pvp,
           SUM(g.deaths_planes) AS deaths_planes,
           SUM(g.deaths_helicopters) AS deaths_helicopters,
           SUM(g.deaths_ships) AS deaths_ships,
           SUM(g.deaths_sams) AS deaths_sams,
           SUM(g.deaths_ground) AS deaths_ground
      FROM (SELECT m.server_name,
                   s.player_ucid,
                   s.slot,
                   s.side,
                   s.tail_no,
                   ROUND(SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC')
                                                 - s.hop_on)))) AS playtime,
                   COUNT(*) AS sorties,
                   SUM(s.takeoffs) AS takeoffs,
                   SUM(s.landings) AS landings,
                   SUM(s.kills) AS kills,
                   SUM(s.pvp) AS pvp,
                   SUM(s.crashes) AS crashes,
                   SUM(s.ejections) AS ejections,
                   SUM(s.kills_planes) AS kills_planes,
                   SUM(s.kills_helicopters) AS kills_helicopters,
                   SUM(s.kills_ships) AS kills_ships,
                   SUM(s.kills_sams) AS kills_sams,
                   SUM(s.kills_ground) AS kills_ground,
                   SUM(s.deaths_pvp) AS deaths_pvp,
                   SUM(s.deaths_planes) AS deaths_planes,
                   SUM(s.deaths_helicopters) AS deaths_helicopters,
                   SUM(s.deaths_ships) AS deaths_ships,
                   SUM(s.deaths_sams) AS deaths_sams,
                   SUM(s.deaths_ground) AS deaths_ground
              FROM statistics s
              JOIN missions m ON m.id = s.mission_id
             GROUP BY 1, 2, 3, 4, 5) g
     GROUP BY 1, 2;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_site_pilot_totals ON mv_site_pilot_totals (server_name, player_ucid);
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_site_pilot_aircraft AS
    SELECT m.server_name,
           s.player_ucid,
           s.slot,
           COALESCE(SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC')
                                            - s.hop_on))), 0)::bigint AS playtime
      FROM statistics s
      JOIN missions m ON m.id = s.mission_id
     GROUP BY 1, 2, 3;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_site_pilot_aircraft ON mv_site_pilot_aircraft (server_name, player_ucid, slot);
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_site_missions AS
    SELECT m.server_name, COUNT(*)::bigint AS missions
      FROM missions m
     WHERE EXISTS (SELECT 1 FROM statistics s WHERE s.mission_id = m.id)
     GROUP BY 1;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_site_missions ON mv_site_missions (server_name);
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_site_aircraft AS
    SELECT m.server_name,
           s.slot,
           COALESCE(SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on))), 0)::bigint AS playtime,
           COALESCE(SUM(s.takeoffs), 0)::bigint AS sorties
      FROM statistics s JOIN missions m ON s.mission_id = m.id
     GROUP BY 1, 2;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_site_aircraft ON mv_site_aircraft (server_name, slot);
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_site_hours AS
    SELECT m.server_name,
           EXTRACT(ISODOW FROM h.hour_start)::smallint AS dow,
           EXTRACT(HOUR FROM h.hour_start)::smallint AS hour,
           EXTRACT(EPOCH FROM SUM(h.overlap))::bigint AS seconds,
           COUNT(*)::bigint AS sessions
      FROM statistics s
      JOIN missions m ON m.id = s.mission_id
     CROSS JOIN LATERAL (
             SELECT gs AS hour_start,
                    LEAST(COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC'), gs + interval '1 hour')
                    - GREATEST(s.hop_on, gs) AS overlap
               FROM generate_series(
                       date_trunc('hour', s.hop_on),
                       date_trunc('hour', COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC')),
                       interval '1 hour') AS gs) AS h
     WHERE h.overlap > interval '0'
     GROUP BY 1, 2, 3;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_site_hours ON mv_site_hours (server_name, dow, hour);
-- Locking refresh on purpose: the loader executes tables.sql inside a transaction block, where the
-- online refresh form is refused. The view is empty until its first refresh, so fill it before the
-- installation ends. mv_serverstats is built from mv_statistics, so it is filled in the same place.
REFRESH MATERIALIZED VIEW mv_site_hours;
REFRESH MATERIALIZED VIEW mv_serverstats;
