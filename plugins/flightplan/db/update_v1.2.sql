-- Flight Plan Plugin v1.2 Update
-- Add support for Mach number cruise speed (e.g., "M0.85")

-- Make migration idempotent - only alter if column is not already TEXT
DO $
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
END $;
