-- Logistics Plugin Database Schema
-- Tables for managing logistics tasks with in-game integration

-- Core logistics tasks table
CREATE TABLE IF NOT EXISTS logistics_tasks (
    id SERIAL PRIMARY KEY,
    server_name TEXT NOT NULL,
    created_by_ucid TEXT,                      -- NULL for admin-created tasks
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, approved, assigned, in_progress, completed, failed, cancelled
    priority TEXT NOT NULL DEFAULT 'normal', -- low, normal, high, urgent
    cargo_type TEXT NOT NULL,                -- Human-readable cargo description
    cargo_items JSONB,                       -- Structured cargo data: [{item: "...", qty: N}, ...]
    source_name TEXT NOT NULL,               -- Airbase/FARP/carrier name
    source_position JSONB,                   -- {x, y, z} coordinates
    destination_name TEXT NOT NULL,          -- Airbase/FARP/carrier name
    destination_position JSONB,              -- {x, y, z} coordinates
    waypoints JSONB,                         -- Optional VIA points: [{name, x, y, z}, ...]
    coalition INTEGER NOT NULL,              -- 1=RED, 2=BLUE
    deadline TIMESTAMP,                      -- Need-by date/time
    assigned_ucid TEXT,                      -- Player who accepted the task
    assigned_at TIMESTAMP,
    completed_at TIMESTAMP,
    notes TEXT,
    remarks TEXT,                            -- Additional pilot instructions (from creator)
    created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    updated_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    approved_by TEXT,                        -- Discord user ID who approved
    approved_at TIMESTAMP,
    discord_message_id BIGINT,               -- Discord message ID for status board posts
    FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (created_by_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE SET NULL,
    FOREIGN KEY (assigned_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_logistics_tasks_server ON logistics_tasks(server_name);
CREATE INDEX IF NOT EXISTS idx_logistics_tasks_status ON logistics_tasks(status);
CREATE INDEX IF NOT EXISTS idx_logistics_tasks_coalition ON logistics_tasks(coalition);
CREATE INDEX IF NOT EXISTS idx_logistics_tasks_assigned ON logistics_tasks(assigned_ucid);

-- Task history/audit trail
CREATE TABLE IF NOT EXISTS logistics_tasks_history (
    id SERIAL PRIMARY KEY,
    task_id INTEGER NOT NULL,
    event TEXT NOT NULL,                     -- created, approved, denied, assigned, started, completed, failed, cancelled, abandoned
    actor_ucid TEXT,                         -- Player who performed the action (in-game)
    actor_discord_id BIGINT,                 -- Discord user (for admin actions)
    details JSONB,                           -- Additional event-specific data
    created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    FOREIGN KEY (task_id) REFERENCES logistics_tasks (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_logistics_tasks_history_task ON logistics_tasks_history(task_id);

-- F10 map markers tracking (for cleanup on task completion/mission restart)
CREATE TABLE IF NOT EXISTS logistics_markers (
    id SERIAL PRIMARY KEY,
    server_name TEXT NOT NULL,
    task_id INTEGER NOT NULL,
    marker_id INTEGER NOT NULL,              -- DCS marker ID
    marker_type TEXT NOT NULL,               -- route_line, source_marker, dest_marker, waypoint_marker
    created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES logistics_tasks (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_logistics_markers_server ON logistics_markers(server_name);
CREATE INDEX IF NOT EXISTS idx_logistics_markers_task ON logistics_markers(task_id);

-- Logbook integration: track completed logistics tasks per pilot
CREATE TABLE IF NOT EXISTS logbook_logistics_completions (
    id SERIAL PRIMARY KEY,
    player_ucid TEXT NOT NULL,
    task_id INTEGER NOT NULL,
    cargo_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    destination_name TEXT NOT NULL,
    completed_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES logistics_tasks (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_logbook_logistics_player ON logbook_logistics_completions(player_ucid);
