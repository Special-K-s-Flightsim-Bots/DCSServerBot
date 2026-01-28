-- Migration v1.4: Fix pilot_logbook_stats view to sum historical + DCS hours
-- Previously used GREATEST() which only took the larger value, causing
-- pilots with both historical and DCS hours to show incorrect totals.

CREATE OR REPLACE VIEW pilot_logbook_stats AS
SELECT
    p.ucid,
    p.name,
    p.discord_id,
    COALESCE(h.total_hours, 0) +
        ROUND(COALESCE(SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, NOW() AT TIME ZONE 'utc') - s.hop_on))) / 3600.0, 0)::DECIMAL, 2)
    AS total_hours,
    COALESCE(SUM(s.kills), 0) AS total_kills,
    COALESCE(SUM(s.deaths), 0) AS total_deaths,
    COALESCE(SUM(s.takeoffs), 0) AS total_takeoffs,
    COALESCE(SUM(s.landings), 0) AS total_landings,
    COALESCE(SUM(s.ejections), 0) AS total_ejections,
    COALESCE(SUM(s.crashes), 0) AS total_crashes,
    h.aircraft_hours AS historical_aircraft_hours
FROM players p
LEFT JOIN statistics s ON p.ucid = s.player_ucid
LEFT JOIN (
    SELECT player_ucid, SUM(total_hours) AS total_hours,
           jsonb_object_agg(COALESCE(imported_from, 'unknown'), aircraft_hours) AS aircraft_hours
    FROM logbook_historical_hours
    GROUP BY player_ucid
) h ON p.ucid = h.player_ucid
GROUP BY p.ucid, p.name, p.discord_id, h.total_hours, h.aircraft_hours;
