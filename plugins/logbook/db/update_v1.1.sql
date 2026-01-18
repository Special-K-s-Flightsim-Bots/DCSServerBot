-- Add ribbon_image column to store pre-generated ribbon PNG
ALTER TABLE logbook_awards ADD COLUMN IF NOT EXISTS ribbon_image BYTEA;
