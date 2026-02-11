-- Flight Plan Plugin v1.1 Update
-- Fix: ETD/ETA timezone handling - use TIMESTAMPTZ to preserve UTC times

ALTER TABLE flightplan_plans
    ALTER COLUMN etd TYPE TIMESTAMPTZ USING etd AT TIME ZONE 'UTC',
    ALTER COLUMN eta TYPE TIMESTAMPTZ USING eta AT TIME ZONE 'UTC';
