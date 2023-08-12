# Plugin "Admin"
This plugin supports administrative commands that are needed to operate a DCS server remotely.

## Configuration
Currently, most of the configuration affecting the admin plugin. is still being done in the dcsserverbot.ini.<br>
An exception is the .download command. You can specify, which folders / patterns you want to offer your admins to
download from your server. There is a default list being loaded, if no list is provided. The format is self-explanatory.

```json
{
  "configs": [
    {
      "downloads": [
        { "label": "DCS Logs", "directory": "%USERPROFILE%\\Saved Games\\{server.installation}\\logs", "pattern": "dcs.log*", "target": "<id:{config[ADMIN_CHANNEL]}>" },
        { "label": "DCSServerBot Logs", "directory": ".", "pattern": "dcsserverbot.log*", "target": "%USERPROFILE%\\Downloads" },
        { "label": "Missions", "directory": "%USERPROFILE%\\Saved Games\\{server.installation}\\Missions", "pattern": "*.miz" },
        { "label": "Tacview", "directory": "%USERPROFILE%\\Documents\\Tacview", "pattern": "Tacview-*.acmi", "target": "<id:12345678901234567>" },
        { "label": "Config Files", "directory": ".\\config", "pattern": "*.json" },
        { "label": "dcsserverbot.ini", "directory": ".\\config", "pattern": "dcsserverbot.ini" }
      ]
    }
  ]
}
```
When using the .download command, you can select which "label" you want to download.<br/>
If "target" is not provided, the file will be sent as a DM. If sending as a DM exceeds the limits of 8 MB, it tries to 
download to the current channel.</br>
"target" can be anything from a file path to a channel (see example above).

## Discord Commands

| Command   | Parameter             | Channel       | Role      | Description                                                                                                             |
|-----------|------------------------------|---------------|-----------|-------------------------------------------------------------------------------------------------------------------------|
| .update   | [-force]                     | admin-channel | DCS Admin | Updates DCS World to the latest available version. -force can be used, if no update could be detected automatically.    |
| .config   |                              | admin-channel | DCS Admin | Configure name, description, password and num max players for your server.                                              |
| .password | [coalition]*                 | admin-channel | DCS Admin | Changes the password of a DCS server or a specific coalition* on this server.                                           |
| .kick     | name [reason]                | admin-channel | DCS Admin | Kicks the user with the in-game name "name" from the DCS server. The "reason" will be provided to the user.             |
| .spec     | name [reason]                | admin-channel | DCS Admin | Moves the user with the in-game name "name" to spectators. The "reason" will be provided to the user as a chat message. |
| .ban      | @member/ucid [days] [reason] | all           | DCS Admin | Bans a specific player either by their Discord ID or UCID for the given amount of days (optional).                      |
| .unban    | @member/ucid                 | all           | DCS Admin | Unbans a specific player either by their Discord ID or UCID.                                                            |
| .bans     |                              | all           | DCS Admin | Lists the current active bans.                                                                                          |
| .download |                              | admin-channel | DCS Admin | Download a dcs.log, dcsserverbot.log, bot config file or a mission into a DM, path or configured channel.               |
| .shell    |                              | admin-channel | Admin     | Runs a shell command on a specific node.                                                                                |

In addition, you can upload embeds to discord channels, just by using json files like this:

```json
{
  "message_id": 967120121632006228,
  "title": "Special K successfully landed at Senaki!",
  "description": "Special K did it again and succeeded at his try to land at Senaki.",
  "img": "https://i.chzbgr.com/full/8459987200/hB315ED4E/damn-instruction-manual",
  "fields": [
    {
      "name": "Pilot",
      "value": "sexy as hell",
      "inline": true
    },
    {
      "name": "Speed",
      "value": "130 kn",
      "inline": true
    },
    {
      "name": "Wind",
      "value": "calm",
      "inline": true
    }
  ],
  "footer": "Just kidding, they forgot to put their gear down!"
}
```

Just upload a file with such a content and a .json extension to the channel where you want the information to be 
displayed (can be done by the Admin-Role only!). If you provide a valid message_id, the message will be updated.

*) DCS 2.7.12 or higher

## Config File Uploads
Every config file that either the bot uses for itself (dcsserverbot.ini) or the different plugins use (_<plugin>_.json)
can be uploaded in the admin channels by a user belonging to the Admin group. The files will be replaced, 
the dedicated plugin will be reloaded or the bot will be restarted (security question applies), if you update the 
dcsserverbot.ini. **All changes will happen on the bot that is controlling the server of that admin channel!**

## Tables
### Bans
| Column    | Type                             | Description                                          |
|-----------|----------------------------------|------------------------------------------------------|
| #ucid     | TEXT NOT NULL                    | Unique ID of this player. FK to the players table.   |
| banned_by | TEXT NOT NULL                    | User name that banned or DCSServerBot for auto bans. |
| reason    | TEXT                             | Reason for the ban.                                  |
| banned_at | TIMESTAMP NOT NULL DEFAULT NOW() | When was that user banned.                           |
