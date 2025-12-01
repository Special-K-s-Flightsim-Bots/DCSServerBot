CREATE TABLE music_playlists(
    name TEXT NOT NULL,
    song_id INTEGER NOT NULL,
    song_file TEXT NOT NULL
);
CREATE TABLE music_radios(
    server_name TEXT NOT NULL,
    radio_name TEXT NOT NULL,
    playlist_name TEXT NOT NULL,
    PRIMARY KEY (server_name, radio_name),
    FOREIGN KEY (server_name) REFERENCES servers (server_name) ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE SEQUENCE music_song_id_seq;
