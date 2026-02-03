-- Logbook v1.5: Migrate squadron tables to userstats shared tables
-- This migration moves data from logbook_squadrons/logbook_squadron_members
-- to the userstats squadrons/squadron_members tables, then drops the old tables.
--
-- Schema version: v4 (simplified per SpecialK's feedback)
-- - squadrons: uses co_ucid/xo_ucid for permissions (no abbreviation/service)
-- - squadron_members: no admin column, permissions via CO/XO lookup
-- - Regular index on player_ucid (not unique) for multi-squadron membership

-- 1a. Pre-flight check: verify userstats has the required columns
DO $$
BEGIN
    -- Check for co_ucid column (indicates SpecialK has updated userstats)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'squadrons' AND column_name = 'co_ucid'
    ) THEN
        RAISE EXCEPTION 'squadrons.co_ucid column missing — upgrade userstats first (requires SpecialK schema update)';
    END IF;
    -- Check for position column in squadron_members
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'squadron_members' AND column_name = 'position'
    ) THEN
        RAISE EXCEPTION 'squadron_members.position column missing — upgrade userstats first (requires SpecialK schema update)';
    END IF;
END $$;

-- 1b. Drop unique index on player_ucid to allow multi-squadron membership
DROP INDEX IF EXISTS idx_squadron_members;

-- 1c. Create regular index for player lookups (if not exists)
CREATE INDEX IF NOT EXISTS idx_squadron_members_player ON squadron_members (player_ucid);

-- 1d. Clean bad UCID data before migration (invalid hex strings)
DELETE FROM logbook_squadron_members
WHERE player_ucid !~ '^[0-9a-fA-F]{20,}$';

-- 1e. Migrate squadrons to shared table
INSERT INTO squadrons (name, description, image_url, co_ucid, xo_ucid, locked)
SELECT name, description, logo_url, co_ucid, xo_ucid, FALSE
FROM logbook_squadrons
ON CONFLICT (name) DO UPDATE SET
    co_ucid = EXCLUDED.co_ucid,
    xo_ucid = EXCLUDED.xo_ucid,
    description = COALESCE(EXCLUDED.description, squadrons.description),
    image_url = COALESCE(EXCLUDED.image_url, squadrons.image_url);

-- 1f. Migrate members to shared table
INSERT INTO squadron_members (squadron_id, player_ucid, position, joined_at)
SELECT s.id, lsm.player_ucid, lsm.position, lsm.joined_at
FROM logbook_squadron_members lsm
JOIN logbook_squadrons ls ON lsm.squadron_id = ls.id
JOIN squadrons s ON ls.name = s.name
WHERE lsm.player_ucid ~ '^[0-9a-fA-F]{20,}$'
ON CONFLICT (squadron_id, player_ucid) DO UPDATE SET
    position = EXCLUDED.position,
    joined_at = EXCLUDED.joined_at;

-- 1g. Create metadata table for logbook-specific fields (abbreviation/service)
-- These are not in the shared userstats schema but may be needed by logbook
CREATE TABLE IF NOT EXISTS logbook_squadron_metadata (
    squadron_id INTEGER PRIMARY KEY REFERENCES squadrons(id) ON DELETE CASCADE,
    abbreviation TEXT,
    service TEXT
);

-- 1h. Migrate abbreviation/service to metadata table
INSERT INTO logbook_squadron_metadata (squadron_id, abbreviation, service)
SELECT s.id, ls.abbreviation, ls.service
FROM logbook_squadrons ls
JOIN squadrons s ON ls.name = s.name
WHERE ls.abbreviation IS NOT NULL OR ls.service IS NOT NULL
ON CONFLICT (squadron_id) DO UPDATE SET
    abbreviation = EXCLUDED.abbreviation,
    service = EXCLUDED.service;

-- 1i. Drop old tables (CASCADE handles dependent objects like logbook_stores_requests)
DROP TABLE IF EXISTS logbook_stores_requests CASCADE;
DROP TABLE IF EXISTS logbook_squadron_members CASCADE;
DROP TABLE IF EXISTS logbook_squadrons CASCADE;
