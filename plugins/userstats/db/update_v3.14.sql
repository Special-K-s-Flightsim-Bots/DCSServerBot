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
REFRESH MATERIALIZED VIEW mv_site_hours;
REFRESH MATERIALIZED VIEW mv_serverstats;
