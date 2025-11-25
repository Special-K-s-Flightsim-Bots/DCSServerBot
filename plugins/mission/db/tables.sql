CREATE TABLE IF NOT EXISTS players (
    ucid TEXT PRIMARY KEY,
    discord_id BIGINT NOT NULL DEFAULT -1,
    name TEXT,
    manual BOOLEAN NOT NULL DEFAULT FALSE,
    first_seen TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    last_seen TIMESTAMP,
    vip BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_players_discord_id ON players(discord_id);
CREATE TABLE players_hist (
    id SERIAL PRIMARY KEY,
    ucid TEXT NOT NULL,
    discord_id BIGINT NOT NULL,
    name TEXT,
    manual BOOLEAN NOT NULL,
    time TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    FOREIGN KEY (ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE INDEX idx_players_hist_discord_id ON players_hist(discord_id);
CREATE INDEX idx_players_hist_ucid ON players_hist(ucid);
CREATE OR REPLACE FUNCTION player_hist_change()
RETURNS trigger
AS $$
BEGIN
    INSERT INTO players_hist(ucid, discord_id, name, manual)
    SELECT OLD.ucid, OLD.discord_id, OLD.name, COALESCE(OLD.manual, FALSE);
    RETURN NEW;
END;
$$ LANGUAGE 'plpgsql';
CREATE TRIGGER tgr_player_update AFTER UPDATE OF discord_id, name, manual ON players FOR EACH ROW EXECUTE PROCEDURE player_hist_change();
CREATE TABLE IF NOT EXISTS bans (
    ucid TEXT PRIMARY KEY,
    banned_by TEXT NOT NULL,
    reason TEXT,
    banned_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    banned_until TIMESTAMP NOT NULL DEFAULT TO_DATE('99991231','YYYYMMDD')
);
CREATE TABLE IF NOT EXISTS bans_hist (
    ucid TEXT NOT NULL,
    banned_by TEXT NOT NULL,
    reason TEXT,
    banned_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    banned_until TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    auto_unban BOOLEAN DEFAULT FALSE, PRIMARY KEY(ucid, banned_at)
);
CREATE OR REPLACE FUNCTION bans_hist_change()
RETURNS trigger
AS $$
BEGIN
    INSERT INTO bans_hist(ucid, banned_by, reason, banned_at, banned_until, auto_unban)
    SELECT OLD.ucid, OLD.banned_by, OLD.reason, OLD.banned_at, (NOW() AT TIME ZONE 'utc'), (OLD.banned_until < (now() AT TIME ZONE 'utc'))
	ON CONFLICT DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE 'plpgsql';
CREATE TRIGGER tgr_bans_update AFTER UPDATE OR DELETE ON bans FOR EACH ROW EXECUTE PROCEDURE bans_hist_change();
CREATE TABLE IF NOT EXISTS missions (
    id SERIAL PRIMARY KEY,
    server_name TEXT NOT NULL,
    mission_name TEXT NOT NULL,
    mission_theatre TEXT NOT NULL,
    mission_start TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    mission_end TIMESTAMP,
    FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS watchlist(
    player_ucid TEXT PRIMARY KEY,
    reason TEXT,
    created_by TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'utc'),
    FOREIGN KEY (player_ucid) REFERENCES players (ucid) ON UPDATE CASCADE ON DELETE CASCADE
);
