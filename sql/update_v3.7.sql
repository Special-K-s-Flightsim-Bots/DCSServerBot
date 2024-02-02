ALTER TABLE nodes ALTER COLUMN last_seen SET DEFAULT (now() AT TIME ZONE 'utc');
ALTER TABLE instances ALTER COLUMN last_seen SET DEFAULT (now() AT TIME ZONE 'utc');
ALTER TABLE audit ALTER COLUMN time SET DEFAULT (now() AT TIME ZONE 'utc');
UPDATE version SET version='v3.8';
