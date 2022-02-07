UPDATE plugins SET version = 'v1.1' WHERE plugin = 'punishment';
ALTER TABLE pu_events ADD COLUMN decay_run INTEGER NOT NULL DEFAULT -1;
