UPDATE plugins SET version = 'v1.1' WHERE plugin = 'userstats';
DELETE FROM statistics WHERE slot = '?';
