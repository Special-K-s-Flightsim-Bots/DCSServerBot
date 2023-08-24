---
title: Music
parent: Plugin System
nav_order: 0
---

# Plugin "Music"
With this plugin, you can add music to your servers.</br>
Currently it only supports mp3 files and SRS as a music destination ("sink" in my terms).

The plugin comes with a nice music player that you can run by using .music in your admin channels.

## Configuration
```json
{
  "configs": [
    {
      "music_dir": ".\\Music",    -- directory below Saved Games\<instance>\ to store your music in
      "sink": {
        "type": "SRSSink",        -- currently, only SRSSink is supported (DiscordSink yet to come)
        "frequency": "40.0",      -- Sink-specific configuration, in this case for SRS
        "modulation": "FM",
        "coalition": "2",
        "volume": "1.0",
        "name": "My Music Box",
        "popup": "Now playing on SRS {frequency} {modulation}: {song}"  -- OPTIONAL, send a popup to DCS on new songs
      }
    }
  ]
}
```

## Discord Commands

| Command   | Parameter         | Channel       | Role      | Description                                                     |
|-----------|-------------------|---------------|-----------|-----------------------------------------------------------------|
| .music    |                   | admin-channel | DCS Admin | The music player. Assign a playlist to your server and play it. |
| .playlist |                   | all           | DCS Admin | Manage playlists.                                               |
| /add_song | <playlist> <song> | all           | DCS Admin | Add a song to a playlist (or create a new one with that song).  |
| /del_song | <playlist> <song> | all           | DCS Admin | Delete a song from a playlist.                                  |

## Database Tables

- [MUSIC](../database.md#music)
