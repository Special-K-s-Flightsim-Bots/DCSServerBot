INSERT INTO plugins (plugin, version) VALUES ('gamemaster', 'v1.0') ON CONFLICT (plugin) DO NOTHING;
