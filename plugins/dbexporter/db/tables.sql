INSERT INTO plugins (plugin, version) VALUES ('dbexporter', 'v1.0') ON CONFLICT (plugin) DO NOTHING;
