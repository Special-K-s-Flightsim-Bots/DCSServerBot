UPDATE music_playlists SET song_file = (regexp_matches(song_file, '[^\\]+$'))[1];
