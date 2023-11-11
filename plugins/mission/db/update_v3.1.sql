ALTER TABLE missions ALTER COLUMN mission_start SET DEFAULT (now() AT TIME ZONE 'utc');
UPDATE missions SET mission_start=mission_start at time zone current_setting('TIMEZONE') at time zone 'utc';
UPDATE bans b SET ucid = sub.new_ucid FROM (SELECT p1.ucid as old_ucid, p2.ucid AS new_ucid FROM players p1, (SELECT DISTINCT ON(discord_id) discord_id, name, ucid, last_seen FROM players WHERE discord_id != -1 AND manual is true ORDER BY discord_id, last_seen DESC) p2 WHERE p1.discord_id = p2.discord_id AND p1.last_seen <> p2.last_seen) AS sub WHERE b.ucid = sub.old_ucid;
