ALTER TABLE players ADD COLUMN IF NOT EXISTS first_seen TIMESTAMP DEFAULT now();
UPDATE players p SET first_seen = (SELECT min(time) FROM players_hist WHERE ucid = p.ucid);
