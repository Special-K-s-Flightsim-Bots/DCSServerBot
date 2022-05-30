INSERT INTO plugins (plugin, version) VALUES ('scheduler', 'v1.1') ON CONFLICT (plugin) DO NOTHING;
