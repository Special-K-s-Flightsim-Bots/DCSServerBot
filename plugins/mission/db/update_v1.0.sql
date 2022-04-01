UPDATE plugins SET version = 'v1.1' WHERE plugin = 'mission';
ALTER TABLE players ADD COLUMN ipaddr TEXT;
