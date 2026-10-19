ALTER TABLE bans ADD COLUMN server_name TEXT;
ALTER TABLE bans DROP CONSTRAINT bans_pkey;
CREATE UNIQUE INDEX bans_ucid_server_uidx ON bans (ucid, COALESCE(server_name, ''));
ALTER TABLE bans_hist ADD COLUMN server_name TEXT;
ALTER TABLE bans_hist DROP CONSTRAINT bans_hist_pkey;
CREATE UNIQUE INDEX bans_hist_ucid_banned_at_server_uidx
    ON bans_hist (ucid, banned_at, COALESCE(server_name, ''));
CREATE OR REPLACE FUNCTION bans_hist_change()
RETURNS trigger
AS $$
BEGIN
    INSERT INTO bans_hist(ucid, banned_by, reason, banned_at, banned_until, auto_unban, server_name)
    SELECT OLD.ucid, OLD.banned_by, OLD.reason, OLD.banned_at, (NOW() AT TIME ZONE 'utc'), (OLD.banned_until < (now() AT TIME ZONE 'utc')), OLD.server_name
	ON CONFLICT DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE 'plpgsql';
