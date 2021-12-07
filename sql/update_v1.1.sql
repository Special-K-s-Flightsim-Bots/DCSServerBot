CREATE TABLE IF NOT EXISTS bans (ucid TEXT PRIMARY KEY, banned_by TEXT NOT NULL, reason TEXT, banned_at TIMESTAMP NOT NULL DEFAULT NOW());
INSERT INTO bans SELECT ucid, 'DCSServerBot', 'n/a' FROM players WHERE ban = true;
CREATE TABLE players_tmp (ucid TEXT PRIMARY KEY, discord_id BIGINT);
INSERT INTO players_tmp SELECT ucid, discord_id FROM players;
DROP TABLE players;
ALTER TABLE players_tmp RENAME TO players;
UPDATE version SET version='v1.2';
