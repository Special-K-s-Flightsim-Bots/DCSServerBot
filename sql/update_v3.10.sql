ALTER TABLE message_persistence ADD COLUMN IF NOT EXISTS thread BIGINT NULL;
UPDATE version SET version='v3.11';
