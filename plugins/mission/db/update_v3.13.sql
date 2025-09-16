CREATE OR REPLACE FUNCTION bans_hist_change()
RETURNS trigger
AS $$
BEGIN
    INSERT INTO bans_hist(ucid, banned_by, reason, banned_at, banned_until, auto_unban)
    SELECT OLD.ucid, OLD.banned_by, OLD.reason, OLD.banned_at, (NOW() AT TIME ZONE 'utc'), (OLD.banned_until < (now() AT TIME ZONE 'utc'))
	ON CONFLICT DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE 'plpgsql';
