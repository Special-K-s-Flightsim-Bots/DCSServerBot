-- Logbook v1.5: Migrate squadron tables to userstats shared tables
-- This migration moves data from logbook_squadrons/logbook_squadron_members
-- to the userstats squadrons/squadron_members tables, then drops the old tables.
--
-- This migration is SELF-CONTAINED: it adds required columns to userstats tables
-- if they don't exist, making it work without requiring upstream schema changes.
--
-- The migration is IDEMPOTENT: safe to run multiple times.

-- Step 1: Verify userstats squadrons table exists (required dependency)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'squadrons') THEN
        RAISE EXCEPTION 'userstats plugin must be installed first. The squadrons table does not exist.';
    END IF;
END $$;

-- Step 2: Add columns to userstats.squadrons (if not already present)
-- These extend the userstats schema for logbook's CO/XO functionality
ALTER TABLE squadrons ADD COLUMN IF NOT EXISTS co_ucid TEXT;
ALTER TABLE squadrons ADD COLUMN IF NOT EXISTS xo_ucid TEXT;

-- Add FK constraints for CO/XO (only if columns exist but constraints don't)
DO $$
BEGIN
    -- Add co_ucid FK if not present
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'squadrons' AND column_name = 'co_ucid'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'squadrons_co_ucid_fkey'
    ) THEN
        ALTER TABLE squadrons
        ADD CONSTRAINT squadrons_co_ucid_fkey
        FOREIGN KEY (co_ucid) REFERENCES players (ucid)
        ON UPDATE CASCADE ON DELETE SET NULL;
    END IF;

    -- Add xo_ucid FK if not present
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'squadrons' AND column_name = 'xo_ucid'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'squadrons_xo_ucid_fkey'
    ) THEN
        ALTER TABLE squadrons
        ADD CONSTRAINT squadrons_xo_ucid_fkey
        FOREIGN KEY (xo_ucid) REFERENCES players (ucid)
        ON UPDATE CASCADE ON DELETE SET NULL;
    END IF;
END $$;

-- Step 3: Add columns to userstats.squadron_members (if not already present)
ALTER TABLE squadron_members ADD COLUMN IF NOT EXISTS position TEXT;

-- joined_at needs special handling for NOT NULL with default
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'squadron_members' AND column_name = 'joined_at'
    ) THEN
        ALTER TABLE squadron_members ADD COLUMN joined_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'utc');
        -- Set default for existing rows then make NOT NULL
        UPDATE squadron_members SET joined_at = (NOW() AT TIME ZONE 'utc') WHERE joined_at IS NULL;
        ALTER TABLE squadron_members ALTER COLUMN joined_at SET NOT NULL;
    END IF;
END $$;

-- Step 4: Drop unique index to allow multi-squadron membership
-- (userstats v3.7+ already dropped this, but be safe)
DROP INDEX IF EXISTS idx_squadron_members;

-- Step 5: Create regular index for player lookups (if not exists)
CREATE INDEX IF NOT EXISTS idx_squadron_members_player ON squadron_members (player_ucid);

-- Step 6: Create metadata table for logbook-specific fields (abbreviation/service)
CREATE TABLE IF NOT EXISTS logbook_squadron_metadata (
    squadron_id INTEGER PRIMARY KEY REFERENCES squadrons(id) ON DELETE CASCADE,
    abbreviation TEXT,
    service TEXT
);

-- Step 7: Check if source tables exist (skip data migration if already done)
DO $$
DECLARE
    source_exists BOOLEAN;
    migrated_count INTEGER;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_name = 'logbook_squadrons'
    ) INTO source_exists;

    IF NOT source_exists THEN
        RAISE NOTICE 'logbook_squadrons table does not exist - migration already complete, skipping data migration';
        RETURN;
    END IF;

    -- Step 8: Clean bad UCID data before migration (invalid hex strings)
    DELETE FROM logbook_squadron_members
    WHERE player_ucid !~ '^[0-9a-fA-F]{20,}$';

    -- Step 9: Clean orphaned CO/XO references (UCIDs not in players table)
    UPDATE logbook_squadrons SET co_ucid = NULL
    WHERE co_ucid IS NOT NULL AND co_ucid NOT IN (SELECT ucid FROM players);

    UPDATE logbook_squadrons SET xo_ucid = NULL
    WHERE xo_ucid IS NOT NULL AND xo_ucid NOT IN (SELECT ucid FROM players);

    -- Step 10: Migrate squadrons to shared table
    INSERT INTO squadrons (name, description, image_url, co_ucid, xo_ucid, locked)
    SELECT name, description, logo_url, co_ucid, xo_ucid, FALSE
    FROM logbook_squadrons
    ON CONFLICT (name) DO UPDATE SET
        co_ucid = COALESCE(EXCLUDED.co_ucid, squadrons.co_ucid),
        xo_ucid = COALESCE(EXCLUDED.xo_ucid, squadrons.xo_ucid),
        description = COALESCE(EXCLUDED.description, squadrons.description),
        image_url = COALESCE(EXCLUDED.image_url, squadrons.image_url);

    GET DIAGNOSTICS migrated_count = ROW_COUNT;
    RAISE NOTICE 'Migrated % squadrons to shared table', migrated_count;

    -- Step 11: Migrate members to shared table
    INSERT INTO squadron_members (squadron_id, player_ucid, position, joined_at, admin)
    SELECT s.id, lsm.player_ucid, lsm.position,
           COALESCE(lsm.joined_at, NOW() AT TIME ZONE 'utc'),
           -- Set admin=TRUE for CO/XO members
           (ls.co_ucid = lsm.player_ucid OR ls.xo_ucid = lsm.player_ucid)
    FROM logbook_squadron_members lsm
    JOIN logbook_squadrons ls ON lsm.squadron_id = ls.id
    JOIN squadrons s ON ls.name = s.name
    WHERE lsm.player_ucid ~ '^[0-9a-fA-F]{20,}$'
      AND lsm.player_ucid IN (SELECT ucid FROM players)
    ON CONFLICT (squadron_id, player_ucid) DO UPDATE SET
        position = COALESCE(EXCLUDED.position, squadron_members.position),
        joined_at = LEAST(EXCLUDED.joined_at, squadron_members.joined_at),
        admin = squadron_members.admin OR EXCLUDED.admin;

    GET DIAGNOSTICS migrated_count = ROW_COUNT;
    RAISE NOTICE 'Migrated % squadron members to shared table', migrated_count;

    -- Step 12: Migrate abbreviation/service to metadata table
    INSERT INTO logbook_squadron_metadata (squadron_id, abbreviation, service)
    SELECT s.id, ls.abbreviation, ls.service
    FROM logbook_squadrons ls
    JOIN squadrons s ON ls.name = s.name
    WHERE ls.abbreviation IS NOT NULL OR ls.service IS NOT NULL
    ON CONFLICT (squadron_id) DO UPDATE SET
        abbreviation = COALESCE(EXCLUDED.abbreviation, logbook_squadron_metadata.abbreviation),
        service = COALESCE(EXCLUDED.service, logbook_squadron_metadata.service);

    GET DIAGNOSTICS migrated_count = ROW_COUNT;
    RAISE NOTICE 'Migrated % squadron metadata records', migrated_count;

    -- Step 13: Drop old tables
    -- Note: logbook_stores_requests is deprecated (superseded by logistics plugin)
    DROP TABLE IF EXISTS logbook_stores_requests CASCADE;
    DROP TABLE IF EXISTS logbook_squadron_members CASCADE;
    DROP TABLE IF EXISTS logbook_squadrons CASCADE;

    RAISE NOTICE 'Dropped legacy logbook squadron tables';
END $$;
