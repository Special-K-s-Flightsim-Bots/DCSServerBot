-- Flight Plan Plugin Database Schema

-- Flight plans table
CREATE TABLE IF NOT EXISTS flightplan_plans (
    id SERIAL PRIMARY KEY,
    player_ucid TEXT NOT NULL,
    server_name TEXT,
    filed_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    departure TEXT,
    destination TEXT,
    alternate TEXT,
    aircraft_type TEXT,
    callsign TEXT,
    route TEXT,
    remarks TEXT,
    status TEXT NOT NULL DEFAULT 'filed',
    etd TIMESTAMPTZ,
    eta TIMESTAMPTZ,
    cruise_altitude INTEGER,
    cruise_speed TEXT,  -- Can be knots (e.g., "450") or Mach (e.g., "M0.85")
    waypoints JSONB,
    departure_position JSONB,
    destination_position JSONB,
    alternate_position JSONB,
    activated_at TIMESTAMP,
    completed_at TIMESTAMP,
    stale_at TIMESTAMP,
    discord_message_id BIGINT,
    coalition INTEGER DEFAULT 0,
    FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_flightplan_plans_ucid ON flightplan_plans (player_ucid);
CREATE INDEX IF NOT EXISTS idx_flightplan_plans_status ON flightplan_plans (status);
CREATE INDEX IF NOT EXISTS idx_flightplan_plans_filed_at ON flightplan_plans (filed_at);
CREATE INDEX IF NOT EXISTS idx_flightplan_plans_server ON flightplan_plans (server_name);
CREATE INDEX IF NOT EXISTS idx_flightplan_plans_stale ON flightplan_plans (stale_at)
    WHERE status IN ('filed', 'active');

-- User-defined named waypoints
CREATE TABLE IF NOT EXISTS flightplan_waypoints (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    created_by_ucid TEXT,
    position_x DOUBLE PRECISION NOT NULL,
    position_z DOUBLE PRECISION NOT NULL,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    altitude INTEGER,
    description TEXT,
    map_theater TEXT,
    is_public BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'utc'),
    FOREIGN KEY (created_by_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_flightplan_waypoints_name_theater ON flightplan_waypoints (LOWER(name), map_theater);
CREATE INDEX IF NOT EXISTS idx_flightplan_waypoints_ucid ON flightplan_waypoints (created_by_ucid);

-- Navigation fixes (VORs, NDBs, TACANs, intersections)
CREATE TABLE IF NOT EXISTS flightplan_navigation_fixes (
    id SERIAL PRIMARY KEY,
    identifier TEXT NOT NULL,
    name TEXT,
    fix_type TEXT NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    position_x DOUBLE PRECISION,
    position_z DOUBLE PRECISION,
    map_theater TEXT NOT NULL,
    frequency TEXT,
    source TEXT,
    UNIQUE(identifier, map_theater)
);
CREATE INDEX IF NOT EXISTS idx_flightplan_navigation_fixes_theater ON flightplan_navigation_fixes (map_theater);
CREATE INDEX IF NOT EXISTS idx_flightplan_navigation_fixes_type ON flightplan_navigation_fixes (fix_type);

-- F10 map markers tracking
CREATE TABLE IF NOT EXISTS flightplan_markers (
    id SERIAL PRIMARY KEY,
    server_name TEXT NOT NULL,
    flight_plan_id INTEGER NOT NULL,
    marker_id INTEGER NOT NULL,
    marker_type TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'utc'),
    expires_at TIMESTAMP,
    FOREIGN KEY (flight_plan_id) REFERENCES flightplan_plans (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_flightplan_markers_plan ON flightplan_markers (flight_plan_id);
CREATE INDEX IF NOT EXISTS idx_flightplan_markers_server ON flightplan_markers (server_name);
CREATE INDEX IF NOT EXISTS idx_flightplan_markers_expires ON flightplan_markers (expires_at)
    WHERE expires_at IS NOT NULL;
