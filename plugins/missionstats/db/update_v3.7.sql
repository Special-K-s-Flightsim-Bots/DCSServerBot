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
