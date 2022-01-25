INSERT INTO plugins (plugin, version) VALUES ('help', 'v1.0') ON CONFLICT (plugin) DO NOTHING;
