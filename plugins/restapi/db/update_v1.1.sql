DROP MATERIALIZED VIEW IF EXISTS mv_serverstats;
CREATE MATERIALIZED VIEW mv_serverstats AS
SELECT m.server_name, COUNT(DISTINCT p.ucid) AS "totalPlayers",
       ROUND(SUM(EXTRACT(EPOCH FROM(COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on))) / 3600)::INTEGER AS "totalPlaytime",
       ROUND(AVG(EXTRACT(EPOCH FROM(COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on))))::INTEGER AS "avgPlaytime",
       SUM(CASE WHEN s.hop_off IS NULL THEN 1 ELSE 0 END) AS "activePlayers",
       COUNT(*) AS "totalSorties",
       SUM(s.kills) AS "totalKills",
       SUM(s.deaths) AS "totalDeaths",
       SUM(s.pvp) AS "totalPvPKills",
       SUM(s.deaths_pvp) AS "totalPvPDeaths",
       NOW() AT TIME ZONE 'UTC' as "timestamp"
FROM players p
JOIN statistics s ON p.ucid = s.player_ucid
JOIN missions m on s.mission_id = m.id
GROUP BY 1;
