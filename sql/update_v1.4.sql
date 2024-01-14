ALTER TABLE servers ADD COLUMN IF NOT EXISTS blue_password TEXT, ADD COLUMN IF NOT EXISTS red_password TEXT;
UPDATE version SET version='v1.5';
