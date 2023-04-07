UPDATE music_playlists SET song_file = (regexp_match(song_file, '[^\\]+$'))[1];
