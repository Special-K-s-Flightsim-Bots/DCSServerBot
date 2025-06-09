CREATE TABLE IF NOT EXISTS tm_available_times (
    time_id SERIAL PRIMARY KEY,
    tournament_id INTEGER REFERENCES tm_tournaments(tournament_id) ON DELETE CASCADE,
    start_time TIME NOT NULL,
    UNIQUE(tournament_id, start_time)
);
ALTER TABLE tm_squadrons ADD COLUMN IF NOT EXISTS application TEXT;
CREATE TABLE IF NOT EXISTS tm_squadron_time_preferences (
    tournament_id INTEGER REFERENCES tm_tournaments(tournament_id) ON DELETE CASCADE,
    squadron_id INTEGER,
    available_time_id INTEGER REFERENCES tm_available_times(time_id),
    PRIMARY KEY (squadron_id, available_time_id),
	FOREIGN KEY (tournament_id, squadron_id) REFERENCES tm_squadrons(tournament_id, squadron_id) ON DELETE CASCADE
);
DROP TABLE IF EXISTS tm_statistics;
