-- Logistics Plugin Database Migration v1.2
-- Makes created_by_ucid nullable to support admin-created tasks

ALTER TABLE logistics_tasks ALTER COLUMN created_by_ucid DROP NOT NULL;
