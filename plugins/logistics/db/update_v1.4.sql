-- Logistics v1.4: Add remarks field for additional task instructions
-- Requested by mailman_digitalkibbles - allows providing instructions
-- separate from the cargo description.

ALTER TABLE logistics_tasks ADD COLUMN IF NOT EXISTS remarks TEXT;
