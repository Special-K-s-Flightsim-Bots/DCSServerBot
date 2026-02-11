ALTER TABLE squadrons ADD COLUMN IF NOT EXISTS co_ucid TEXT NULL REFERENCES players(ucid) ON UPDATE CASCADE ON DELETE SET NULL;
ALTER TABLE squadrons ADD COLUMN IF NOT EXISTS xo_ucid TEXT NULL REFERENCES players(ucid) ON UPDATE CASCADE ON DELETE SET NULL;
ALTER TABLE squadron_members ADD COLUMN IF NOT EXISTS rank TEXT NULL;
ALTER TABLE squadron_members ADD COLUMN IF NOT EXISTS position TEXT NULL;
ALTER TABLE squadron_members ADD COLUMN IF NOT EXISTS joined_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC');
UPDATE squadrons s SET co_ucid = (SELECT player_ucid FROM squadron_members m WHERE s.id = m.squadron_id AND admin IS TRUE LIMIT 1);
ALTER TABLE squadron_members DROP COLUMN IF EXISTS admin;
CREATE OR REPLACE FUNCTION clear_co_xo_on_member_delete()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_TABLE_NAME = 'squadron_members' AND TG_OP = 'DELETE' THEN
    UPDATE squadrons
       SET co_ucid = NULL
     WHERE id = OLD.squadron_id
       AND co_ucid = OLD.player_ucid;

    UPDATE squadrons
       SET xo_ucid = NULL
     WHERE id = OLD.squadron_id
       AND xo_ucid = OLD.player_ucid;
  END IF;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_clear_co_xo AFTER DELETE ON squadron_members FOR EACH ROW EXECUTE FUNCTION clear_co_xo_on_member_delete();
