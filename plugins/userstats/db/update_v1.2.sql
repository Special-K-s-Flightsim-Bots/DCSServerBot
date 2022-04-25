UPDATE plugins SET version = 'v1.3' WHERE plugin = 'userstats';
ALTER TABLE statistics ADD COLUMN side INTEGER DEFAULT 0;
