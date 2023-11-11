ALTER TABLE missions ALTER COLUMN mission_start SET DEFAULT (now() AT TIME ZONE 'utc');
UPDATE missions SET mission_start=mission_start at time zone current_setting('TIMEZONE') at time zone 'utc';
