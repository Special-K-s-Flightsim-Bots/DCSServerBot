-- Create logbook_pilots table for pilot-specific service and rank
CREATE TABLE IF NOT EXISTS logbook_pilots (
    player_ucid TEXT PRIMARY KEY,
    service TEXT,
    rank TEXT,
    FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE
);

-- Add service column to squadrons (the squadron's parent service)
ALTER TABLE logbook_squadrons ADD COLUMN IF NOT EXISTS service TEXT;

-- Migrate rank from squadron_members to logbook_pilots
-- Takes the rank from the most recent squadron membership
INSERT INTO logbook_pilots (player_ucid, rank)
SELECT DISTINCT ON (player_ucid) player_ucid, rank
FROM logbook_squadron_members
WHERE rank IS NOT NULL
ORDER BY player_ucid, joined_at DESC
ON CONFLICT (player_ucid) DO UPDATE SET rank = EXCLUDED.rank WHERE logbook_pilots.rank IS NULL;

-- Populate squadron service based on naming conventions
UPDATE logbook_squadrons SET service = 'RN' WHERE name LIKE '%NAS%' AND service IS NULL;
UPDATE logbook_squadrons SET service = 'AAC' WHERE name LIKE '%AAC%' OR name LIKE '656%' AND service IS NULL;
UPDATE logbook_squadrons SET service = 'RAF' WHERE name LIKE '%Sqn%' AND service IS NULL;

-- Infer pilot service from their squadron's service (can be manually corrected later)
UPDATE logbook_pilots lp
SET service = (
    SELECT s.service
    FROM logbook_squadron_members sm
    JOIN logbook_squadrons s ON sm.squadron_id = s.id
    WHERE sm.player_ucid = lp.player_ucid
    ORDER BY sm.joined_at DESC
    LIMIT 1
)
WHERE lp.service IS NULL;

-- Drop rank column from squadron_members (no longer needed there)
ALTER TABLE logbook_squadron_members DROP COLUMN IF EXISTS rank;
