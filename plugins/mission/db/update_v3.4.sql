CREATE TABLE IF NOT EXISTS bans_hist (ucid TEXT NOT NULL, banned_by TEXT NOT NULL, reason TEXT, banned_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'), banned_until TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'), auto_unban BOOLEAN DEFAULT FALSE, PRIMARY KEY(ucid, banned_at));
CREATE OR REPLACE FUNCTION bans_hist_change() RETURNS trigger AS $$ BEGIN INSERT INTO bans_hist(ucid, banned_by, reason, banned_at, banned_until, auto_unban) SELECT OLD.ucid, OLD.banned_by, OLD.reason, OLD.banned_at, (now() AT TIME ZONE 'utc'), (OLD.banned_until < (now() AT TIME ZONE 'utc')); RETURN NEW; END; $$ LANGUAGE 'plpgsql';
DROP TRIGGER IF EXISTS tgr_bans_update ON bans;
CREATE TRIGGER tgr_bans_update AFTER UPDATE OR DELETE ON bans FOR EACH ROW EXECUTE PROCEDURE bans_hist_change();
