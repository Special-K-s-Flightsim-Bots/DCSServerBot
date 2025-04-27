CREATE TABLE tm_tournaments (
    tournament_id SERIAL PRIMARY KEY,
    campaign TEXT NOT NULL,
    rounds INTEGER NOT NULL DEFAULT 2,
    num_players INTEGER NOT NULL DEFAULT 4,
    FOREIGN KEY (campaign) REFERENCES campaigns(name) ON DELETE CASCADE
);
CREATE UNIQUE INDEX idx_tm_tournaments_campaign ON tm_tournaments(campaign);
CREATE TABLE tm_squadrons (
    tournament_id INTEGER NOT NULL,
    squadron_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING', -- 'PENDING', 'ACCEPTED', 'REJECTED'
    PRIMARY KEY(tournament_id, squadron_id),
    FOREIGN KEY (tournament_id) REFERENCES tm_tournaments(tournament_id) ON DELETE CASCADE
);
CREATE TABLE tm_matches (
    match_id SERIAL PRIMARY KEY,
    tournament_id INTEGER NOT NULL,
    server_name TEXT NOT NULL,
    squadron_red INTEGER NOT NULL,
    squadron_blue INTEGER NOT NULL,
    round_number INTEGER NOT NULL DEFAULT 0,
    choices_red_ack BOOLEAN DEFAULT FALSE,
    choices_blue_ack BOOLEAN DEFAULT FALSE,
    squadron_red_rounds_won INTEGER NOT NULL DEFAULT 0,
    squadron_blue_rounds_won INTEGER NOT NULL DEFAULT 0,
    winner_squadron_id INTEGER,
    FOREIGN KEY (tournament_id) REFERENCES tm_tournaments(tournament_id) ON DELETE CASCADE,
    FOREIGN KEY (tournament_id, squadron_red) REFERENCES tm_squadrons(tournament_id, squadron_id) ON DELETE CASCADE,
    FOREIGN KEY (tournament_id, squadron_blue) REFERENCES tm_squadrons(tournament_id, squadron_id) ON DELETE CASCADE,
    FOREIGN KEY (tournament_id, winner_squadron_id) REFERENCES tm_squadrons(tournament_id, squadron_id) ON DELETE CASCADE
);
CREATE TABLE tm_choices (
    match_id INTEGER,
    squadron_id INTEGER,
    preset TEXT NOT NULL,
    num INTEGER NOT NULL,
    PRIMARY KEY (match_id, squadron_id, preset),
    FOREIGN KEY (match_id) REFERENCES tm_matches(match_id) ON DELETE CASCADE
);
CREATE TABLE tm_statistics (
    squadron_id INTEGER NOT NULL,
    tournament_id INTEGER NOT NULL,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    matches_won INTEGER NOT NULL DEFAULT 0,
    matches_lost INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (squadron_id, tournament_id),
    FOREIGN KEY (tournament_id, squadron_id) REFERENCES tm_squadrons(tournament_id, squadron_id) ON DELETE CASCADE
);
