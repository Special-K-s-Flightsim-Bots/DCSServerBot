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
Unlike other plugins, the configuration for this plugin is stored in the database. You still have a json file though,
but it is only used as a default configuration, as long as you haven't changed anything yet. Keep that in mind.

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
        "name": "My Music Box"
      }
    }
  ]
}
```

## Discord Commands

| Command | Parameter                        | Channel        | Role       | Description                                                       |
|---------|----------------------------------|----------------|------------|-------------------------------------------------------------------|
| .music  | -clear                           | admin-channel  | DCS Admin  | The music player. Create your serverside playlist. Or -clear it.  |

**Attention:** "period" can either be a period [day, week, month, year] or a campaign name!

## Database Tables

- [MUSIC](../database.md#music)
