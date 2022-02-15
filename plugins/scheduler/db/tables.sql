INSERT INTO plugins (plugin, version) VALUES ('scheduler', 'v1.0') ON CONFLICT (plugin) DO NOTHING;
