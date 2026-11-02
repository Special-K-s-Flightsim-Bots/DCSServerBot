-- The console's Audit page reads the trail newest first, one page at a time. Without an index on the
-- order it always asks for, every page sorts the whole table.
-- IF NOT EXISTS keeps a re-run (a fresh install lands one version behind) idempotent.
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit (time DESC);
UPDATE version SET version='v3.23';
