INSERT INTO plugins (plugin, version) VALUES ('motd', 'v1.0') ON CONFLICT (plugin) DO NOTHING;
