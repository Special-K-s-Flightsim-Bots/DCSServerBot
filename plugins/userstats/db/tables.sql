CREATE TABLE IF NOT EXISTS statistics (mission_id INTEGER NOT NULL, player_ucid TEXT NOT NULL, slot TEXT NOT NULL, tail_no TEXT NULL, side INTEGER DEFAULT 0, kills INTEGER DEFAULT 0, pvp INTEGER DEFAULT 0, deaths INTEGER DEFAULT 0, ejections INTEGER DEFAULT 0, crashes INTEGER DEFAULT 0, teamkills INTEGER DEFAULT 0, kills_planes INTEGER DEFAULT 0, kills_helicopters INTEGER DEFAULT 0, kills_ships INTEGER DEFAULT 0, kills_sams INTEGER DEFAULT 0, kills_ground INTEGER DEFAULT 0, deaths_pvp INTEGER DEFAULT 0, deaths_planes INTEGER DEFAULT 0, deaths_helicopters INTEGER DEFAULT 0, deaths_ships INTEGER DEFAULT 0, deaths_sams INTEGER DEFAULT 0, deaths_ground INTEGER DEFAULT 0, takeoffs INTEGER DEFAULT 0, landings INTEGER DEFAULT 0, hop_on TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'), hop_off TIMESTAMP, PRIMARY KEY (mission_id, player_ucid, slot, hop_on));
CREATE INDEX IF NOT EXISTS idx_statistics_player_ucid ON statistics(player_ucid);
CREATE TABLE IF NOT EXISTS squadrons (id SERIAL PRIMARY KEY, name TEXT NOT NULL, description TEXT NULL, role BIGINT NULL, image_url TEXT NULL, channel BIGINT NULL, locked BOOLEAN NOT NULL DEFAULT FALSE);
CREATE UNIQUE INDEX IF NOT EXISTS idx_squadrons_name ON squadrons (name);
CREATE TABLE IF NOT EXISTS squadron_members (squadron_id INTEGER NOT NULL, player_ucid TEXT NOT NULL, admin BOOLEAN NOT NULL DEFAULT FALSE, PRIMARY KEY (squadron_id, player_ucid));
CREATE UNIQUE INDEX IF NOT EXISTS idx_squadron_members ON squadron_members (player_ucid);
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_statistics AS
    SELECT s.player_ucid, m.server_name, s.slot, s.tail_no, s.side,
       COUNT(s.slot) AS usage, SUM(s.kills) AS kills, SUM(s.pvp) AS pvp, SUM(s.deaths) AS deaths, SUM(s.ejections) AS ejections, SUM(s.crashes) AS crashes, SUM(s.teamkills) AS teamkills, SUM(s.takeoffs) AS takeoffs, SUM(s.landings) AS landings,
	   SUM(s.kills_planes) AS kills_planes, SUM(s.kills_helicopters) AS kills_helicopters, SUM(s.kills_ships) AS kills_ships, SUM(s.kills_sams) AS kills_sams, SUM(s.kills_ground) AS kills_ground,
	   SUM(s.deaths_pvp) AS deaths_pvp, SUM(s.deaths_planes) AS deaths_planes, SUM(s.deaths_helicopters) AS deaths_helicopters, SUM(s.deaths_ships) AS deaths_ships, SUM(s.deaths_sams) AS deaths_sams, SUM(s.deaths_ground) AS deaths_ground,
	   ROUND(SUM(EXTRACT(EPOCH FROM(COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on)))) AS playtime
    FROM statistics s JOIN missions m ON s.mission_id = m.id
    GROUP BY 1, 2, 3, 4, 5;
CREATE INDEX IF NOT EXISTS idx_mv_statistics_ucid ON mv_statistics (player_ucid);
CREATE INDEX IF NOT EXISTS idx_mv_statistics_tail_no ON mv_statistics (tail_no);
