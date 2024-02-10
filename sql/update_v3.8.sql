ALTER TABLE intercom ALTER COLUMN time SET DEFAULT (now() AT TIME ZONE 'utc');
ALTER TABLE files ALTER COLUMN created SET DEFAULT (now() AT TIME ZONE 'utc');
UPDATE version SET version='v3.9';
