ALTER TABLE servers ADD COLUMN last_seen TIMESTAMP DEFAULT NOW();
UPDATE version SET version='v1.6';
