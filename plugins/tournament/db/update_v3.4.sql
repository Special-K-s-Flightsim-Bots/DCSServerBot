CREATE TABLE tm_squadron_terrain_preferences (
    tournament_id INTEGER REFERENCES tm_tournaments(tournament_id) ON DELETE CASCADE,
    squadron_id INTEGER,
    terrain TEXT NOT NULL,
    PRIMARY KEY (squadron_id, terrain),
	FOREIGN KEY (tournament_id, squadron_id) REFERENCES tm_squadrons(tournament_id, squadron_id) ON DELETE CASCADE
);
