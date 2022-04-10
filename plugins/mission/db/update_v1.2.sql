UPDATE plugins SET version = 'v1.3' WHERE plugin = 'mission';
ALTER TABLE players ADD COLUMN manual BOOLEAN DEFAULT FALSE;
