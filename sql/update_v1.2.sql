ALTER TABLE players ADD COLUMN name TEXT, ADD COLUMN last_seen TIMESTAMP;
UPDATE players p SET last_seen = (SELECT MAX(hop_off) FROM statistics WHERE player_ucid = p.ucid);
UPDATE version SET version='v1.3';
