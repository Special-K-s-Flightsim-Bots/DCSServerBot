ALTER TABLE greenieboard ALTER COLUMN time SET DEFAULT (now() AT TIME ZONE 'utc');
