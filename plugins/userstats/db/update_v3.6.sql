ALTER TABLE squadron_members ADD COLUMN admin BOOLEAN NOT NULL DEFAULT FALSE;
CREATE UNIQUE INDEX IF NOT EXISTS idx_squadron_members ON squadron_members (player_ucid);
