ALTER TABLE serverstats ALTER COLUMN time SET DEFAULT (now() AT TIME ZONE 'utc');
