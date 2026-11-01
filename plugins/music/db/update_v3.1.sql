DELETE FROM music_playlists p
USING music_playlists q
WHERE p.name = q.name AND p.song_file = q.song_file AND p.song_id > q.song_id;
ALTER TABLE music_playlists ADD CONSTRAINT music_playlists_name_song_file_key UNIQUE (name, song_file);
