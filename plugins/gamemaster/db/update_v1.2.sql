CREATE TABLE IF NOT EXISTS coalitions (server_name TEXT NOT NULL, player_ucid TEXT NOT NULL, coalition TEXT, coalition_leave TIMESTAMP, PRIMARY KEY(server_name, player_ucid));
UPDATE players SET coalition = NULL, coalition_leave = NULL;
