CREATE TABLE IF NOT EXISTS squadrons (id SERIAL PRIMARY KEY, name TEXT NOT NULL, description TEXT NULL, role BIGINT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS idx_squadrons_name ON squadrons (name);
CREATE TABLE IF NOT EXISTS squadron_members (squadron_id INTEGER NOT NULL, player_ucid TEXT NOT NULL, PRIMARY KEY (squadron_id, player_ucid));
