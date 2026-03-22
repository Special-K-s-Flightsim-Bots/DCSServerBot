-- Migration v1.3: Remove duplicate flight_plans table
-- Flight plans are now managed by the dedicated flightplan plugin.
-- This migration moves any existing data and drops the legacy table.

-- Migrate existing flight plans to the flightplan plugin's table (if it exists)
-- Uses a DO block to handle the case where flightplan_plans doesn't exist yet
DO $$
BEGIN
    -- Check if both tables exist
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'logbook_flight_plans')
       AND EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'flightplan_plans') THEN
        -- Migrate data that doesn't already exist
        INSERT INTO flightplan_plans (
            player_ucid, filed_at, departure, destination, alternate,
            aircraft_type, callsign, route, remarks, status
        )
        SELECT
            player_ucid, filed_at, departure, destination, alternate,
            aircraft_type, callsign, route, remarks, status
        FROM logbook_flight_plans lfp
        WHERE NOT EXISTS (
            SELECT 1 FROM flightplan_plans fp
            WHERE fp.player_ucid = lfp.player_ucid
            AND fp.filed_at = lfp.filed_at
            AND COALESCE(fp.callsign, '') = COALESCE(lfp.callsign, '')
        );

        RAISE NOTICE 'Migrated flight plans from logbook_flight_plans to flightplan_plans';
    ELSIF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'logbook_flight_plans') THEN
        RAISE NOTICE 'flightplan_plans table does not exist yet, dropping logbook_flight_plans without migration';
    END IF;
END $$;

-- Drop the legacy table and its indexes (if they exist)
DROP INDEX IF EXISTS idx_logbook_flight_plans_ucid;
DROP INDEX IF EXISTS idx_logbook_flight_plans_status;
DROP INDEX IF EXISTS idx_logbook_flight_plans_filed_at;
DROP TABLE IF EXISTS logbook_flight_plans;
