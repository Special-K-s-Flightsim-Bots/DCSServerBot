UPDATE plugins SET version = 'v1.2' WHERE plugin = 'slotblocking';
ALTER TABLE sb_points RENAME TO credits;
