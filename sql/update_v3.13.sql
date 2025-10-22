ALTER TABLE cluster ADD COLUMN IF NOT EXISTS guild_name TEXT;
UPDATE version SET version='v3.14';
