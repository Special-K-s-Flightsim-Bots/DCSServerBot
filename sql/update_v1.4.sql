ALTER TABLE servers ADD COLUMN blue_password TEXT, ADD COLUMN red_password TEXT;
UPDATE version SET version='v1.5';
