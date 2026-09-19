CREATE TABLE IF NOT EXISTS refuelingstats (
    id SERIAL PRIMARY KEY,
    mission_id INTEGER NOT NULL,
    init_id TEXT NOT NULL,
    init_type TEXT NOT NULL,
    tanker TEXT NOT NULL,
    fuel_taken INTEGER,
    transfer_complete BOOLEAN,
    transfer_time DECIMAL,
    time TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);
CREATE INDEX IF NOT EXISTS idx_refuelingstats_ucid ON refuelingstats(init_id);
