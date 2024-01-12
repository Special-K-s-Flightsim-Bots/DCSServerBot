ALTER TABLE players ADD COLUMN IF NOT EXISTS name TEXT, ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP;
UPDATE players p SET last_seen = (SELECT MAX(hop_off) FROM statistics WHERE player_ucid = p.ucid);
UPDATE version SET version='v1.3';
