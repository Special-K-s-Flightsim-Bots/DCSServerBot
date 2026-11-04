-- The audit entry's actor, as the caller names it. A Discord member already travels in
-- discord_id and a linked player in ucid, but a local web user (the whole point of a no_discord
-- install) has neither, so its label ([web:local/<name>]) is stored here. Existing rows keep NULL:
-- their actor is unrecoverable, and a backfill would have to guess it out of event text.
-- IF NOT EXISTS keeps a re-run (a fresh install lands one version behind) idempotent.
ALTER TABLE audit ADD COLUMN IF NOT EXISTS actor TEXT NULL;
UPDATE version SET version='v3.22';
