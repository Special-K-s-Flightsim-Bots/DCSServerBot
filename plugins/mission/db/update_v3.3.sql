ALTER TABLE players ALTER COLUMN first_seen SET DEFAULT (now() AT TIME ZONE 'utc');
ALTER TABLE bans ALTER COLUMN banned_at SET DEFAULT (now() AT TIME ZONE 'utc');
ALTER TABLE players_hist ALTER COLUMN time SET DEFAULT (now() AT TIME ZONE 'utc');
