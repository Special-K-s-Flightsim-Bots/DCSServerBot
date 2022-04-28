UPDATE plugins SET version = 'v1.4' WHERE plugin = 'mission';
ALTER TABLE players ADD COLUMN coalition TEXT, ADD COLUMN coalition_leave TIMESTAMP;
