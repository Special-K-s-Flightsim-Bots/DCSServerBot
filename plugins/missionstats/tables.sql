INSERT INTO plugins (plugin, version) VALUES ('missionstats', 'v1.0') ON CONFLICT (plugin) DO NOTHING;
