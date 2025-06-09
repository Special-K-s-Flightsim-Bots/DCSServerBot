CREATE TABLE tm_tickets (
    tournament_id INTEGER,
    squadron_id INTEGER,
    ticket_name TEXT NOT NULL,
    ticket_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (tournament_id, squadron_id, ticket_name),
    FOREIGN KEY (tournament_id, squadron_id) REFERENCES tm_squadrons(tournament_id, squadron_id) ON DELETE CASCADE
);
