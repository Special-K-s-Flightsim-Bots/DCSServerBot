ALTER TABLE pu_events ALTER column time SET DEFAULT timezone('utc', now());
DELETE FROM pu_events_sdw;
