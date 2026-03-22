CREATE OR REPLACE FUNCTION player_hist_change()
RETURNS trigger AS $$
BEGIN
  IF NEW.discord_id IS DISTINCT FROM OLD.discord_id
  OR NEW.name       IS DISTINCT FROM OLD.name
  OR NEW.manual     IS DISTINCT FROM OLD.manual THEN

    INSERT INTO players_hist (ucid, discord_id, name, manual, time)
    VALUES (NEW.ucid, NEW.discord_id, NEW.name, NEW.manual, (now() AT TIME ZONE 'UTC'));
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
