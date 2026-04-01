CREATE TABLE tm_players (
    tournament_id INTEGER NOT NULL,
    player_ucid TEXT NOT NULL,
    ip_hash TEXT NULL,
    FOREIGN KEY (tournament_id) REFERENCES tm_tournaments(tournament_id) ON DELETE CASCADE,
    FOREIGN KEY (player_ucid) REFERENCES players(ucid) ON UPDATE CASCADE ON DELETE CASCADE,
    PRIMARY KEY (tournament_id, player_ucid)
);
