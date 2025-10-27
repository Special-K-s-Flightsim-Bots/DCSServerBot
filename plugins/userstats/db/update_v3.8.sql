ALTER TABLE statistics ADD COLUMN IF NOT EXISTS tail_no TEXT NULL;
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_statistics AS
    SELECT s.player_ucid, m.server_name, s.slot, s.tail_no, s.side,
       SUM(s.kills) AS kills, SUM(s.pvp) AS pvp, SUM(s.deaths) AS deaths, SUM(s.ejections) AS ejections, SUM(s.crashes) AS crashes, SUM(s.teamkills) AS teamkills, SUM(s.takeoffs) AS takeoffs, SUM(s.landings) AS landings,
	   SUM(s.kills_planes) AS kills_planes, SUM(s.kills_helicopters) AS kills_helicopters, SUM(s.kills_sams) AS kills_sams, SUM(s.kills_ground) AS kills_ground,
	   SUM(s.deaths_pvp) AS deaths_pvp, SUM(s.deaths_planes) AS deaths_planes, SUM(s.deaths_helicopters) AS deaths_helicopters, SUM(s.deaths_ships) AS deaths_ships, SUM(s.deaths_sams) AS deaths_sams, SUM(s.deaths_ground) AS deaths_ground,
	   ROUND(SUM(EXTRACT(EPOCH FROM(COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on)))) AS playtime
    FROM statistics s JOIN missions m ON s.mission_id = m.id
    GROUP BY 1, 2, 3, 4, 5;
CREATE INDEX idx_mv_statistics_ucid ON mv_statistics (player_ucid);
CREATE INDEX idx_mv_statistics_tail_no ON mv_statistics (tail_no);
