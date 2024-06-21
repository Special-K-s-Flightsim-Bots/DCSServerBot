CREATE TABLE IF NOT EXISTS watchlist(player_ucid TEXT PRIMARY KEY, reason TEXT, created_by TEXT NOT NULL, created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'utc'));
INSERT INTO watchlist (player_ucid, created_by) SELECT ucid, 'n/a' FROM players WHERE watchlist IS TRUE;
ALTER TABLE players DROP COLUMN IF EXISTS watchlist;
