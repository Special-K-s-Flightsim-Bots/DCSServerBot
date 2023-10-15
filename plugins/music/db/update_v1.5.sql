DROP TABLE music_servers;
CREATE TABLE music_radios(server_name TEXT NOT NULL, radio_name TEXT NOT NULL, playlist_name TEXT NOT NULL, PRIMARY KEY (server_name, radio_name));
