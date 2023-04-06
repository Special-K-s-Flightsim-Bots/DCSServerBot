---
title: Discord Role Configuration
parent: Configuration
nav_order: 2
---

# Discord Role Configuration

The bot uses the following **internal** roles to apply specific permissions to commands.
You can change the role names to the ones being used in your Discord. That has to be done in the dcsserverbot.ini 
configuration file. If you want to add multiple groups, separate them by comma (does **not** apply to coalition roles!).

| Role           | Description                                                                                                                                         |
|----------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| DCS            | People with this role are allowed to chat, check their statistics and gather information about running missions and players.                        |
| DCS Admin      | People with this role are allowed to restart missions, managing the mission list, ban and unban people.                                             |
| Admin          | People with this role are allowed to manage the server, start it up, shut it down, update it, change the password and gather the server statistics. |
| GameMaster     | People with this role can see both [Coalitions] and run specific commands that are helpful in missions.                                             |
| Coalition Blue | People with this role are members of the blue coalition (see [Coalitions]).                                                                         |
| Coalition Red  | People with this role are members of the red coalition (see [Coalitions]).                                                                          |

# How to change the default roles for a command
DCSServerBot comes with a pre-defined set of permissions, that should work for the majority of you. If you still want
to amend the permission of a specific command, you can do that by adding a "commands" section to the respective
plugin.json:

Example: admin.json
```json
{
  "commands": {
    "ban": {
      "roles": ["Admin"]
    },
    "unban": {
      "roles": ["Admin"]
    },
    "bans": {
      "roles": ["Admin"]
    }
  },
  "configs": [
    {
      "downloads": [
        { "label": "DCS Logs", "directory": "%USERPROFILE%\\Saved Games\\{server.installation}\\logs", "pattern": "dcs*.log" },
        { "label": "DCSServerBot Logs", "directory": ".", "pattern": "dcsserverbot.log*", "target": "%USERPROFILE%\\Downloads" },
        { "label": "Missions", "directory": "%USERPROFILE%\\Saved Games\\{server.installation}\\Missions", "pattern": "*.miz" },
        { "label": "Tacview", "directory": "%USERPROFILE%\\Documents\\Tacview", "pattern": "Tacview-*.acmi", "target": "<id:{config[ADMIN_CHANNEL]}>" },
        { "label": "Chat Logs", "directory": "%USERPROFILE%\\Saved Games\\{server.installation}\\logs", "pattern": "chat.*log*" },
        { "label": "Config Files", "directory": ".\\config", "pattern": "*.json" },
        { "label": "dcsserverbot.ini", "directory": ".\\config", "pattern": "dcsserverbot.ini" }
      ]
    }
  ]
}
```
This changed the commands "ban", "unban" and "bans" so that only users of the Admin group can call the respective command.

[Coalitions]: coalitions.md
