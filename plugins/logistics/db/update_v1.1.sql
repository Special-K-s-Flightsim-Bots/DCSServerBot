-- Logistics Plugin Database Migration v1.1
-- Adds discord_message_id column for status board posts

ALTER TABLE logistics_tasks ADD COLUMN IF NOT EXISTS discord_message_id BIGINT;
