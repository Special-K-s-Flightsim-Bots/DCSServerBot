-- Pilot search box: serves a PREFIX match (LOWER(name) LIKE 'wag%') only; a contains-match is not indexed by this.
CREATE INDEX IF NOT EXISTS idx_players_name_lower ON players (LOWER(name));
