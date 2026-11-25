-- 3.16: the two site views name their columns after what they hold.
--   mv_site_pilot_totals.sorties was COUNT(*) of the statistics rows: a SESSION count, not a sortie
--   (a sortie is one flight, which only the missionstats event stream can count). It is `sessions`.
--   mv_site_aircraft.sorties was the airframe's takeoff sum. It is `takeoffs`.
-- A materialized view cannot be renamed into place, so both are dropped and rebuilt; the CREATE
-- populates them, so no separate REFRESH is needed. Their unique indexes go with them and are
-- recreated, which is what the plugin's REFRESH ... CONCURRENTLY needs.

DROP MATERIALIZED VIEW IF EXISTS mv_site_pilot_totals;
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_site_pilot_totals AS
    SELECT g.server_name,
           g.player_ucid,
           SUM(g.playtime) AS playtime,
           SUM(g.sessions) AS sessions,
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
                   COUNT(*) AS sessions,
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

DROP MATERIALIZED VIEW IF EXISTS mv_site_aircraft;
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_site_aircraft AS
    SELECT m.server_name,
           s.slot,
           COALESCE(SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on))), 0)::bigint AS playtime,
           COALESCE(SUM(s.takeoffs), 0)::bigint AS takeoffs
      FROM statistics s JOIN missions m ON s.mission_id = m.id
     GROUP BY 1, 2;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_site_aircraft ON mv_site_aircraft (server_name, slot);

-- A rebuilt materialized view has no planner statistics: without this the first reads plan blind and
-- a leaderboard query can take seconds. ANALYZE is cheap on a view this size and is what autovacuum
-- would otherwise only do later.
ANALYZE mv_site_pilot_totals;
ANALYZE mv_site_aircraft;
