CREATE TABLE IF NOT EXISTS plugins (plugin TEXT PRIMARY KEY, version TEXT NOT NULL);
INSERT INTO plugins VALUES ('admin', 'v1.0');
INSERT INTO plugins VALUES ('help', 'v1.0');
INSERT INTO plugins VALUES ('mission', 'v1.0');
INSERT INTO plugins VALUES ('missionstats', 'v1.0');
INSERT INTO plugins VALUES ('userstats', 'v1.0');
UPDATE version SET version='v1.4';
