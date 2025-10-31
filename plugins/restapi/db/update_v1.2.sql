DROP MATERIALIZED VIEW IF EXISTS mv_serverstats;
CREATE MATERIALIZED VIEW mv_serverstats AS
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
