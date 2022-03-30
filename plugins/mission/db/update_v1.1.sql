UPDATE plugins SET version = 'v1.2' WHERE plugin = 'mission';
ALTER TABLE players ADD COLUMN IF NOT EXISTS ipaddr TEXT;
