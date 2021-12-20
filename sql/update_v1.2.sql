ALTER TABLE players ADD COLUMN name TEXT, ADD COLUMN last_seen TIMESTAMP;
UPDATE version SET version='v1.3';
