CREATE TABLE IF NOT EXISTS logbook_pilots (
    player_ucid TEXT PRIMARY KEY,
    service TEXT,
    rank TEXT,
    FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS logbook_squadrons (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    abbreviation TEXT,
    service TEXT,
    description TEXT,
    logo_url TEXT,
    co_ucid TEXT,
    xo_ucid TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    FOREIGN KEY (co_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE SET NULL,
    FOREIGN KEY (xo_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_logbook_squadrons_name ON logbook_squadrons (name);
CREATE INDEX IF NOT EXISTS idx_logbook_squadrons_co_ucid ON logbook_squadrons (co_ucid);
CREATE INDEX IF NOT EXISTS idx_logbook_squadrons_xo_ucid ON logbook_squadrons (xo_ucid);

CREATE TABLE IF NOT EXISTS logbook_squadron_members (
    squadron_id INTEGER NOT NULL,
    player_ucid TEXT NOT NULL,
    position TEXT,
    joined_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    PRIMARY KEY (squadron_id, player_ucid),
    FOREIGN KEY (squadron_id) REFERENCES logbook_squadrons (id) ON DELETE CASCADE,
    FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_logbook_squadron_members_ucid ON logbook_squadron_members (player_ucid);

CREATE TABLE IF NOT EXISTS logbook_qualifications (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    aircraft_type TEXT,
    requirements JSONB,
    valid_days INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_logbook_qualifications_name ON logbook_qualifications (name);
CREATE INDEX IF NOT EXISTS idx_logbook_qualifications_aircraft ON logbook_qualifications (aircraft_type);

CREATE TABLE IF NOT EXISTS logbook_pilot_qualifications (
    player_ucid TEXT NOT NULL,
    qualification_id INTEGER NOT NULL,
    granted_by TEXT,
    granted_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    expires_at TIMESTAMP,
    PRIMARY KEY (player_ucid, qualification_id),
    FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (qualification_id) REFERENCES logbook_qualifications (id) ON DELETE CASCADE,
    FOREIGN KEY (granted_by) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_logbook_pilot_qualifications_ucid ON logbook_pilot_qualifications (player_ucid);
CREATE INDEX IF NOT EXISTS idx_logbook_pilot_qualifications_expires ON logbook_pilot_qualifications (expires_at);

CREATE TABLE IF NOT EXISTS logbook_awards (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    image_url TEXT,
    ribbon_colors JSONB,
    ribbon_image BYTEA,
    auto_grant BOOLEAN NOT NULL DEFAULT FALSE,
    requirements JSONB
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_logbook_awards_name ON logbook_awards (name);

CREATE TABLE IF NOT EXISTS logbook_pilot_awards (
    player_ucid TEXT NOT NULL,
    award_id INTEGER NOT NULL,
    granted_by TEXT,
    granted_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    citation TEXT,
    PRIMARY KEY (player_ucid, award_id, granted_at),
    FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (award_id) REFERENCES logbook_awards (id) ON DELETE CASCADE,
    FOREIGN KEY (granted_by) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_logbook_pilot_awards_ucid ON logbook_pilot_awards (player_ucid);
CREATE INDEX IF NOT EXISTS idx_logbook_pilot_awards_award_id ON logbook_pilot_awards (award_id);

-- NOTE: Flight plans are managed by the dedicated flightplan plugin.
-- See plugins/flightplan/db/tables.sql for the flightplan_plans table.

CREATE TABLE IF NOT EXISTS logbook_stores_requests (
    id SERIAL PRIMARY KEY,
    squadron_id INTEGER NOT NULL,
    requested_by TEXT NOT NULL,
    requested_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    items JSONB,
    status TEXT NOT NULL DEFAULT 'pending',
    approved_by TEXT,
    approved_at TIMESTAMP,
    FOREIGN KEY (squadron_id) REFERENCES logbook_squadrons (id) ON DELETE CASCADE,
    FOREIGN KEY (requested_by) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (approved_by) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_logbook_stores_requests_squadron ON logbook_stores_requests (squadron_id);
CREATE INDEX IF NOT EXISTS idx_logbook_stores_requests_status ON logbook_stores_requests (status);
CREATE INDEX IF NOT EXISTS idx_logbook_stores_requests_requested_by ON logbook_stores_requests (requested_by);

CREATE TABLE IF NOT EXISTS logbook_historical_hours (
    player_ucid TEXT NOT NULL,
    imported_from TEXT,
    total_hours DECIMAL NOT NULL DEFAULT 0,
    aircraft_hours JSONB,
    imported_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    PRIMARY KEY (player_ucid, imported_from),
    FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_logbook_historical_hours_ucid ON logbook_historical_hours (player_ucid);

CREATE OR REPLACE VIEW pilot_logbook_stats AS
SELECT
    p.ucid,
    p.name,
    p.discord_id,
    GREATEST(
        COALESCE(h.total_hours, 0),
        ROUND(COALESCE(SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, NOW() AT TIME ZONE 'utc') - s.hop_on))) / 3600.0, 0)::DECIMAL, 2)
    ) AS total_hours,
    COALESCE(SUM(s.kills), 0) AS total_kills,
    COALESCE(SUM(s.deaths), 0) AS total_deaths,
    COALESCE(SUM(s.takeoffs), 0) AS total_takeoffs,
    COALESCE(SUM(s.landings), 0) AS total_landings,
    COALESCE(SUM(s.ejections), 0) AS total_ejections,
    COALESCE(SUM(s.crashes), 0) AS total_crashes,
    h.aircraft_hours AS historical_aircraft_hours
FROM players p
LEFT JOIN statistics s ON p.ucid = s.player_ucid
LEFT JOIN (
    SELECT player_ucid, SUM(total_hours) AS total_hours,
           jsonb_object_agg(COALESCE(imported_from, 'unknown'), aircraft_hours) AS aircraft_hours
    FROM logbook_historical_hours
    GROUP BY player_ucid
) h ON p.ucid = h.player_ucid
GROUP BY p.ucid, p.name, p.discord_id, h.total_hours, h.aircraft_hours;
