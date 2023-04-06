DROP TABLE music_playlists;
CREATE TABLE music_playlists(name TEXT NOT NULL, song_id INTEGER NOT NULL, song_file TEXT NOT NULL, PRIMARY KEY (name));
CREATE TABLE music_servers(server_name TEXT NOT NULL, playlist_name TEXT NOT NULL, PRIMARY KEY (server_name, playlist_name));
