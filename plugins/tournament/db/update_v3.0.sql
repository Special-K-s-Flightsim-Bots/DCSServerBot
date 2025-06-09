ALTER TABLE tm_matches ADD COLUMN IF NOT EXISTS squadron_red_channel BIGINT DEFAULT -1;
ALTER TABLE tm_matches ADD COLUMN IF NOT EXISTS squadron_blue_channel BIGINT DEFAULT -1;
