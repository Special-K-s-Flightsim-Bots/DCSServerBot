-- Flight Plan Plugin v1.2 Update
-- Add support for Mach number cruise speed (e.g., "M0.85")

ALTER TABLE flightplan_plans
    ALTER COLUMN cruise_speed TYPE TEXT USING cruise_speed::TEXT;
