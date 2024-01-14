ALTER TABLE greenieboard ADD COLUMN IF NOT EXISTS trapcase INTEGER;
UPDATE greenieboard SET trapcase = CASE WHEN night = FALSE THEN 1 ELSE 3 END;
