CREATE TABLE tm_tournaments (
    tournament_id SERIAL PRIMARY KEY,
    campaign TEXT NOT NULL,
    rounds INTEGER NOT NULL DEFAULT 3,
    num_players INTEGER NOT NULL DEFAULT 4,
    FOREIGN KEY (campaign) REFERENCES campaigns(name) ON DELETE CASCADE
);
CREATE UNIQUE INDEX idx_tm_tournaments_campaign ON tm_tournaments(campaign);
CREATE TABLE tm_available_times (
    time_id SERIAL PRIMARY KEY,
    tournament_id INTEGER NOT NULL,
    start_time TIME NOT NULL,
    UNIQUE(tournament_id, start_time),
    FOREIGN KEY (tournament_id) REFERENCES tm_tournaments(tournament_id) ON DELETE CASCADE
);
CREATE TABLE tm_squadrons (
    tournament_id INTEGER NOT NULL,
    squadron_id INTEGER NOT NULL,
    application TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING', -- 'PENDING', 'ACCEPTED', 'REJECTED', 'WITHDRAW'
    group_number INTEGER,
    PRIMARY KEY(tournament_id, squadron_id),
    FOREIGN KEY (tournament_id) REFERENCES tm_tournaments(tournament_id) ON DELETE CASCADE,
    FOREIGN KEY (squadron_id) REFERENCES squadrons(id) ON DELETE CASCADE
);
CREATE TABLE tm_squadron_time_preferences (
    tournament_id INTEGER NOT NULL,
    squadron_id INTEGER NOT NULL,
    available_time_id INTEGER NOT NULL,
    PRIMARY KEY (squadron_id, available_time_id),
    FOREIGN KEY (available_time_id) REFERENCES tm_available_times(time_id),
	FOREIGN KEY (tournament_id, squadron_id) REFERENCES tm_squadrons(tournament_id, squadron_id) ON DELETE CASCADE
);
CREATE TABLE tm_squadron_terrain_preferences (
    tournament_id INTEGER NOT NULL,
    squadron_id INTEGER NOT NULL,
    terrain TEXT NOT NULL,
    PRIMARY KEY (squadron_id, terrain),
	FOREIGN KEY (tournament_id, squadron_id) REFERENCES tm_squadrons(tournament_id, squadron_id) ON DELETE CASCADE
);
CREATE TABLE tm_matches (
    match_id SERIAL PRIMARY KEY,
    tournament_id INTEGER NOT NULL,
    stage INTEGER NOT NULL,
    server_name TEXT NOT NULL,
    match_time TIMESTAMP,
    squadron_red INTEGER NOT NULL,
    squadron_blue INTEGER NOT NULL,
    squadron_red_channel BIGINT NOT NULL DEFAULT -1,
    squadron_blue_channel BIGINT NOT NULL DEFAULT -1,
    round_number INTEGER NOT NULL DEFAULT 0,
    choices_red_ack BOOLEAN DEFAULT FALSE,
    choices_blue_ack BOOLEAN DEFAULT FALSE,
    squadron_red_rounds_won INTEGER NOT NULL DEFAULT 0,
    squadron_blue_rounds_won INTEGER NOT NULL DEFAULT 0,
    winner_squadron_id INTEGER,
    FOREIGN KEY (tournament_id) REFERENCES tm_tournaments(tournament_id) ON DELETE CASCADE,
    FOREIGN KEY (server_name) REFERENCES servers(server_name) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (tournament_id, squadron_red) REFERENCES tm_squadrons(tournament_id, squadron_id) ON DELETE CASCADE,
    FOREIGN KEY (tournament_id, squadron_blue) REFERENCES tm_squadrons(tournament_id, squadron_id) ON DELETE CASCADE,
    FOREIGN KEY (tournament_id, winner_squadron_id) REFERENCES tm_squadrons(tournament_id, squadron_id)
);
CREATE TABLE tm_choices (
    match_id INTEGER NOT NULL,
    squadron_id INTEGER NOT NULL,
    preset TEXT NOT NULL,
    config JSON,
    PRIMARY KEY (match_id, squadron_id, preset),
    FOREIGN KEY (match_id) REFERENCES tm_matches(match_id) ON DELETE CASCADE,
    FOREIGN KEY (squadron_id) REFERENCES squadrons(id)
);
CREATE TABLE tm_persistent_choices (
    choice_id SERIAL PRIMARY KEY,
    match_id INTEGER NOT NULL,
    squadron_id INTEGER NOT NULL,
    preset TEXT NOT NULL,
    config JSON,
    FOREIGN KEY (match_id) REFERENCES tm_matches(match_id) ON DELETE CASCADE,
    FOREIGN KEY (squadron_id) REFERENCES squadrons(id)
);
CREATE TABLE tm_tickets (
    tournament_id INTEGER,
    squadron_id INTEGER,
    ticket_name TEXT NOT NULL,
    ticket_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (tournament_id, squadron_id, ticket_name),
    FOREIGN KEY (tournament_id, squadron_id) REFERENCES tm_squadrons(tournament_id, squadron_id) ON DELETE CASCADE
);
