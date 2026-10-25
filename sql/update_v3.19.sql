-- Records which weather preset a running mission was LAUNCHED with. Written by the node that applies
-- the preset at load time; read by the console's server page. NULL / FALSE means the load applied no
-- preset. The record belongs to the NAMED server (the ``servers`` table, keyed by ``server_name``),
-- not to the technical slot an instance occupies: an instance can be rebound from one server to the
-- next, and the record must not follow the slot. IF NOT EXISTS keeps a re-run (a fresh install lands
-- one version behind) idempotent.
ALTER TABLE servers ADD COLUMN IF NOT EXISTS launched_preset TEXT NULL;
ALTER TABLE servers ADD COLUMN IF NOT EXISTS launched_preset_temporary BOOLEAN NOT NULL DEFAULT FALSE;
UPDATE version SET version='v3.20';
