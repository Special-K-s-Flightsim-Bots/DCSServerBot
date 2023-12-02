ALTER TABLE statistics ALTER COLUMN hop_on SET DEFAULT (now() AT TIME ZONE 'utc');
UPDATE statistics SET hop_on=hop_on at time zone current_setting('TIMEZONE') at time zone 'utc';
UPDATE statistics SET hop_off=hop_off at time zone current_setting('TIMEZONE') at time zone 'utc' WHERE hop_off IS NOT NULL;
UPDATE statistics SET player_ucid = sub.new_ucid FROM (SELECT p1.ucid as old_ucid, p2.ucid AS new_ucid FROM players p1, (SELECT DISTINCT ON(discord_id) discord_id, name, ucid, last_seen FROM players WHERE discord_id != -1 AND manual is true ORDER BY discord_id, last_seen DESC) p2 WHERE p1.discord_id = p2.discord_id AND p1.last_seen <> p2.last_seen) AS sub WHERE player_ucid = sub.old_ucid;
