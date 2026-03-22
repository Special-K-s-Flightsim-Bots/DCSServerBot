-- Flight Plan Plugin v1.2 Update
-- Add support for Mach number cruise speed (e.g., "M0.85")
-- Add FK constraints for server_name columns

-- Make migration idempotent - only alter if column is not already TEXT
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'flightplan_plans'
        AND column_name = 'cruise_speed'
        AND data_type != 'text'
    ) THEN
        ALTER TABLE flightplan_plans
            ALTER COLUMN cruise_speed TYPE TEXT USING cruise_speed::TEXT;
    END IF;
END $$;

-- Add FK constraints for server_name (idempotent)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_flightplan_plans_server'
    ) THEN
        ALTER TABLE flightplan_plans
            ADD CONSTRAINT fk_flightplan_plans_server
            FOREIGN KEY (server_name) REFERENCES servers(server_name) ON DELETE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_flightplan_markers_server'
    ) THEN
        ALTER TABLE flightplan_markers
            ADD CONSTRAINT fk_flightplan_markers_server
            FOREIGN KEY (server_name) REFERENCES servers(server_name) ON DELETE CASCADE;
    END IF;
END $$;
