ALTER TABLE pu_events ALTER column time SET DEFAULT timezone('utc', now());
