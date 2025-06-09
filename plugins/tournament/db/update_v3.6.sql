CREATE TABLE tm_persistent_choices (
    choice_id SERIAL PRIMARY KEY,
    match_id INTEGER,
    squadron_id INTEGER,
    preset TEXT NOT NULL,
    config JSON,
    FOREIGN KEY (match_id) REFERENCES tm_matches(match_id) ON DELETE CASCADE
);
ALTER TABLE tm_choices DROP COLUMN num;
ALTER TABLE tm_choices ADD COLUMN config JSON;
