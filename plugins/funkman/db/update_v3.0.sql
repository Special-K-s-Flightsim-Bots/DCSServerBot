CREATE TABLE IF NOT EXISTS strafe_runs (id SERIAL PRIMARY KEY, mission_id INTEGER NOT NULL, player_ucid TEXT NOT NULL, unit_type TEXT NOT NULL,range_name TEXT NOT NULL, accuracy NUMERIC NOT NULL, quality INTEGER, time TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'));
CREATE TABLE IF NOT EXISTS bomb_runs (id SERIAL PRIMARY KEY, mission_id INTEGER NOT NULL, player_ucid TEXT NOT NULL, unit_type TEXT NOT NULL, range_name TEXT NOT NULL, distance NUMERIC NOT NULL, quality INTEGER, time TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'));
CREATE INDEX IF NOT EXISTS idx_strafe_runs_ucid ON strafe_runs(player_ucid);
CREATE INDEX IF NOT EXISTS idx_bomb_runs_ucid ON bomb_runs(player_ucid);
