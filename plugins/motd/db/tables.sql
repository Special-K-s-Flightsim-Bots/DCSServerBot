INSERT INTO plugins (plugin, version) VALUES ('motd', 'v1.1') ON CONFLICT (plugin) DO NOTHING;
