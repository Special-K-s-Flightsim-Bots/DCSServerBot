# Plugin "Music"
This plugin is the Discord interface to the [Music Service](../../services/music/README.md).

## Configuration
See [Music Service](../../services/music/README.md).

## Discord Commands

| Command           | Parameter                    | Channel | Role       | Description                                                    |
|-------------------|------------------------------|---------|------------|----------------------------------------------------------------|
| /music player     | radio_name                   | all     | DCS Admin  | The music player. Assign a radio to your server and play it.   |
| /music play       | radio_name [playlist] [song} | all     | DCS Admin  | Play a song or a complete playlist.                            |
| /music stop       | radio_name                   | all     | DCS Admin  |                                                                |
| /playlist add     | playlist song                | all     | DCS Admin  | Add a song to a playlist (or create a new one with that song). |
| /playlist add_all | playlist                     | all     | DCS Admin  | Adds all songs from the music directory to the playlist.       |
| /playlist delete  | playlist song                | all     | DCS Admin  | Delete a song from a playlist.                                 |

## Database Tables
## MUSIC_PLAYLISTS
| Column      | Type                        | Description                                                 |
|-------------|-----------------------------|-------------------------------------------------------------|
| name        | TEXT                        | The playlists name.                                         |
| song_id     | NUMBER                      | id of the song (for ordering)                               |
| song_file   | TEXT                        | filepath to the song, relative to the main music directory. |

### MUSIC_RADIOS
| Column        | Type          | Description                                                 |
|---------------|---------------|-------------------------------------------------------------|
| #server_name  | TEXT NOT NULL | the server this radio is configured for                     |
| #radio_name   | TEXT NOT NULL | the respective radio                                        |
| playlist_name | TEXT NOT NULL | name of the playlist to play                                |
