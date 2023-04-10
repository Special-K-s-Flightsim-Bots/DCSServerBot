---
title: Backup
parent: Plugin System
nav_order: 0
---

# Plugin "Backup"
With this plugin, you can handle backup operations for your DCSServerBot, your PostgreSQL database and your 
DCS server configurations, mission, scripts, mods, whatever you want.

## Configuration
As per usual, you configure this plugin via JSON. Instead of having a default section and a per-server section,
you create one single backup configuration for your whole bot.

{: .note }
> The database only needs to be backed-up on the Master node!

```json
{
  "target": "G:\\My Drive\\Backup",                       -- where to backup to
  "delete_after": "7",                                    -- number of days to keep your backups or "never" to keep them forever
  "backups": {
    "database": {
      "path": "C:\\Program Files\\PostgreSQL\\14\\bin",
      "password": "secret",                               -- this is the password of your postgres user!
      "schedule": {
        "times": ["03:00"],                               -- you can define multiple times, if you like
        "days": "NNNNNNY"                                 -- on which day the backup should run, "MoTuWeThFrSaSu"
      }
    },
    "servers": {
      "directories": [                                    -- Directories you want to backup from your DCS servers
        "Config",
        "Missions",
        "Scripts",
        "Mods"
      ],
      "schedule": {
        "times": ["02:00"],
        "days": "YNYNYNN"
      }
    },
    "bot": {
      "directories": [
        "config",                                         -- mandatory, your configuration
        "reports"                                         -- optional, only needed, if you have defined custom reports
      ],
      "schedule": {
        "times": ["00:00", "12:00"],
        "days": "YYYYYYY"
      }
    }
  }
}
```

The plugin will create directories for every node and backup date below you target directory.
