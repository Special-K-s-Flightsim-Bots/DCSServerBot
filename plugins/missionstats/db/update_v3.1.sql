ALTER TABLE missionstats ALTER COLUMN time SET DEFAULT (now() AT TIME ZONE 'utc');
