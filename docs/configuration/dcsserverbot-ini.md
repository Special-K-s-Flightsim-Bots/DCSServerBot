---
title: dcsserverbot.ini
parent: Configuration
nav_order: 2
---

# dcsserverbot.ini

The bot configuration is held in `config/dcsserverbot.ini`. See `sample\dcsserverbot.ini` for an example.<br/>
If you run the `install.py` script for the first time, it will generate a basic file for you that you can amend to your needs afterwards.

For some configurations, default values are set in the file `config/default.ini`.

{: .warning }
> **Do not change `config/default.ini`!**<br/>
> And just overwrite settings within `config/dcsserverbot.ini`, if you want to have different value!

The following parameters can be used to configure the bot:

# Section \[BOT\]

| Parameter           | Description                                                                                                                                                                                                                                                                                                                                                                                                          |
|---------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| OWNER               | The Discord ID of the bots owner (that's you!).<br/>If you don't know your ID, go to your Discord profile, make sure "Developer Mode" is enabled under "Advanced", go to "My Account", press the "..." besides your profile picture and select "Copy ID"                                                                                                                                                             |
| TOKEN               | The token to be used to run the bot.<br/>Can be obtained at [http://discord.com/developers](http://discord.com/developers).                                                                                                                                                                                                                                                                                          |
| PUBLIC_IP           | (Optional) Your public IP, if you have a dedicated one, otherwise the bot will determine your current one.                                                                                                                                                                                                                                                                                                           |
| DATABASE_URL        | URL to the PostgreSQL database used to store our data.<br/>**If login fails, check password for any special character!**                                                                                                                                                                                                                                                                                             |
| USE_DASHBOARD       | Whether to use the fancy cmd dashboard or not (for performance reasons over slow RDP connections). Default is true.                                                                                                                                                                                                                                                                                                  |
| COMMAND_PREFIX      | The prefix to be used by Discord commands. Default is '.'                                                                                                                                                                                                                                                                                                                                                            |
| CHAT_COMMAND_PREFIX | The prefix to be used by in-game-chat commands. Default is '-'                                                                                                                                                                                                                                                                                                                                                       |
| HOST                | IP the bot listens on for messages from DCS.<br/>Default is 127.0.0.1, to only accept internal communication on that machine.                                                                                                                                                                                                                                                                                        |
| PORT                | UDP port, the bot listens on for messages from DCS.<br/>Default is 10081. **Don't expose this port to the outside world!**                                                                                                                                                                                                                                                                                           |
| MASTER              | If true, start the bot in master-mode (default for one-bot-installations). If only one bot is running, then there is only a master.\nIf you have to use more than one bot installation, for multiple DCS servers that are spanned over several locations, you have to install one agent (MASTER = false) at every other location. All DCS servers of that location will then automatically register with that agent. |
| MASTER_ONLY         | True, if this is a master-only installation, set to false otherwise.                                                                                                                                                                                                                                                                                                                                                 |
| SLOW_SYSTEM         | If true, some timeouts are increased to allow slower systems to catch up.<br/>Default is false.                                                                                                                                                                                                                                                                                                                      |
| PLUGINS             | List of plugins to be loaded (you usually don't want to touch this).                                                                                                                                                                                                                                                                                                                                                 |
| OPT_PLUGINS         | List of optional plugins to be loaded. Here you can add your plugins that you want to use and that are not loaded by default.                                                                                                                                                                                                                                                                                        |
| AUTOUPDATE          | If true, the bot auto-updates itself with the latest release on startup.                                                                                                                                                                                                                                                                                                                                             |
| AUTOBAN             | If true, members leaving the discord will be automatically banned (default = false).                                                                                                                                                                                                                                                                                                                                 |
| MESSAGE_BAN         | Ban-message to be displayed on DCS join, when people are auto-banned on DCS due to a Discord ban.                                                                                                                                                                                                                                                                                                                    |
| WIPE_STATS_ON_LEAVE | If true, stats will be wiped whenever someone leaves your discord (default = true).                                                                                                                                                                                                                                                                                                                                  |
| AUTOMATCH           | If false, users have to match themselves using the .linkme command.                                                                                                                                                                                                                                                                                                                                                  |
| DISCORD_STATUS      | (Optional) status to be displayed below the bots avatar in Discord.                                                                                                                                                                                                                                                                                                                                                  |
| GREETING_DM         | A greeting message, that people will receive as a DM in Discord, if they join your guild.                                                                                                                                                                                                                                                                                                                            |
| MESSAGE_TIMEOUT     | General timeout for popup messages (default 10 seconds).                                                                                                                                                                                                                                                                                                                                                             |
| MESSAGE_AUTODELETE  | Delete messages after a specific amount of seconds. This is true for all statistics embeds, LSO analysis, greenieboard, but no usual user commands.                                                                                                                                                                                                                                                                  |
| DESANITIZE          | Whether to desanitize MissionScriping.lua or not (default = yes).                                                                                                                                                                                                                                                                                                                                                    |
| AUDIT_CHANNEL       | (Optional) The ID of an audit channel where audit events will be logged into.<br/>For security reasons, it is recommended that no users can delete messages in this channel.                                                                                                                                                                                                                                         |

# Section \[LOGGING\]

| Parameter           | Description                                                                                                |
|---------------------|------------------------------------------------------------------------------------------------------------|
| LOGLEVEL            | The level of logging that is written into the logfile.<br/>Values: DEBUG, INFO, WARNING, ERROR, CRITICAL.  |
| LOGROTATE_COUNT     | Number of logfiles to keep (default: 5).                                                                   |
| LOGROTATE_SIZE      | Number of bytes until which a logfile is rotated (default: 10 MB).                                         |

# Section \[DB\]

| Parameter           | Description                                                      |
|---------------------|------------------------------------------------------------------|
| MASTER_POOL_MIN     | Minimum number of database connections in the pool (on MASTER).  |
| MASTER_POOL_MAX     | Maximum number of database connections in the pool (on MASTER).  |
| AGENT_POOL_MIN      | Minimum number of database connections in the pool (on AGENT).   |
| AGENT_POOL_MAX      | Maximum number of database connections in the pool (on AGENT).   |

# Section \[ROLES\]

| Parameter      | Description                                                                                                                   |
|----------------|-------------------------------------------------------------------------------------------------------------------------------|
| Admin          | The name of the admin role in you Discord.                                                                                    |
| DCS Admin      | The name of the role you'd like to give admin rights on your DCS servers (_Moderator_ for instance).                          |
| DCS            | The role of users being able to see their statistics and mission information (usually the general user role in your Discord). |
| GameMaster     | Members of this role can run commands that affect the mission behaviour or handle coalition specific details.                 |

# Section \[FILTER\] (Optional)

| Parameter      | Description                                                                                                                                                                                                                       |
|----------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| TAG_FILTER     | Many groups have their own tag, that might make it difficult for the bot to match usernames. The usual tags like [Tag], =Tag= or similar ones, are supported already. If you see matching issues, you might want to try this one. |
| SERVER_FILTER  | Filter to shorten server names (if needed)                                                                                                                                                                                        |
| MISSION_FILTER | Filter to shorten mission names (if needed)                                                                                                                                                                                       |
| EVENT_FILTER   | Filter events from the missionstats plugin (optional).<br/>See [DCS_singleton] for a complete list of events.                                                                                                                     |

# Section \[REPORTS\] (Optional)

| Parameter   | Description                                                                                   |
|-------------|-----------------------------------------------------------------------------------------------|
| NUM_WORKERS | Number of threads that render a graph.                                                        |
| CKJ_FONT    | One of TC, JP or KR to support Traditional Chinese, Japanese or Korean characters in reports. |

# Section \[DCS Section\]

| Parameter                       | Description                                                                                                                                   |
|---------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| DCS_INSTALLATION                | The installation directory of DCS World.                                                                                                      |
| AUTOUPDATE                      | If true, your DCS server will be kept up-to-date automatically by the bot (default=false).                                                    |
| GREETING_MESSAGE_MEMBERS        | A greeting message, that people will receive in DCS chat, if they get recognized by the bot as a member of your discord.                      |
| GREETING_MESSAGE_UNMATCHED      | A greeting message, that people will receive in DCS chat, if they are unmatched.                                                              |
| SERVER_USER                     | The username to display as user no. 1 in the server (aka "Observer")                                                                          |
| MAX_HUNG_MINUTES                | The maximum amount in minutes the server is allowed to not respond to the bot until considered dead (default = 3). Set it to 0 to disable it. |
| MESSAGE_PLAYER_USERNAME         | Message that a user gets when using line-feeds or carriage-returns in their names.                                                            |
| MESSAGE_PLAYER_DEFAULT_USERNAME | Message that a user gets when being rejected because of a default player name (Player, Spieler, etc.).                                        |
| MESSAGE_BAN                     | Message a banned user gets when being rejected.                                                                                               |
| MESSAGE_AFK                     | Message for players that got kicked because of being AFK.                                                                                     |
| MESSAGE_SLOT_SPAMMING           | Message for players that got kicked because of slot spamming.                                                                                 |
| MESSAGE_SERVER_FULL             | Message for players that can't join because the server is full and available slots are reserverd for VIPs.                                    |

# Server Specific Sections

This section has to be named **exactly** like your `Saved Games\<instance>` directory. Usual names are `DCS` or `DCS.release_server`.
If your directory is named `DCS` instead (stable version), just add these fields to the DCS category above.

| Parameter                  | Description                                                                                                                                                                                |
|----------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| DCS_HOST                   | The internal (!) IP of the machine, DCS is running onto. If the DCS server is running on the same machine as the bot (default), this should be 127.0.0.1.                                  |
| DCS_PORT                   | Must be a unique value > 1024 of an unused port in your system. This is **NOT** the DCS tcp/udp port (10308), that is used by DCS but a unique different one. Keep the default, if unsure. |
| DCS_HOME                   | The main configuration directory of your DCS server installation (for Hook installation). Keep it empty, if you like to place the Hook by yourself.                                        |
| EVENTS_CHANNEL             | The ID of the channel where in-game events should be shown. If not specified, the CHAT_CHANNEL will be used instead. If set to -1, events will be disabled.                                |
| CHAT_CHANNEL               | The ID of the in-game chat channel to be used for the specific DCS server. Must be unique for every DCS server instance configured. If "-1", no chat messages will be generated.           |
| STATUS_CHANNEL             | The ID of the status-display channel to be used for the specific DCS server. Must be unique for every DCS server instance configured.                                                      |
| ADMIN_CHANNEL              | The ID of the admin-commands channel to be used for the specific DCS server. Must be unique for every DCS server instance configured.                                                      |
| AUTOSCAN                   | Scan for missions in Saved Games\..\Missions and auto-add them to the mission list (default = false).                                                                                      |
| AFK_TIME                   | Number of seconds a player is considered AFK when being on spectators for longer than AFK_TIME seconds. Default is -1 (disabled).                                                          |
| CHAT_LOG                   | true (default), log all chat messages from players in Saved Games\<installation>\Logs\chat.log                                                                                             |
| CHAT_LOGROTATE_COUNT       | Number of chat-logs to keep (default = 10).                                                                                                                                                |
| CHAT_LOGROTATE_SIZE        | Max size of a chat.log until it gets rotated (default 1 MB).                                                                                                                               |
| MISSIONS_DIR               | (Optional) If you want to use a central missions directory for multiple servers, you can set it in here.                                                                                   |
| PING_ADMIN_ON_CRASH        | Define if the role DCS Admin should be pinged when a server crash is being detected (default = true).                                                                                      |
| START_MINIMIZED            | DCS will start minimized as default. You can disabled that by setting this value to false.                                                                                                 |
| STATISTICS                 | If false, no statistics will be generated for this server. Default is true (see [Userstats]).                                                                                              |
| MISSION_STATISTICS         | If true, mission statistics will be generated for all missions loaded in this server (see [Missionstats]).                                                                                 |
| DISPLAY_MISSION_STATISTICS | If true, the persistent mission stats embed is displayed in the servers stats channel (default = true).                                                                                    |
| PERSIST_MISSION_STATISTICS | If true, player data is exported in the missionstats table (default = true).                                                                                                               |
| PERSIST_AI_STATISTICS      | If true, AI data is exported, too (only player data otherwise), default = false.                                                                                                           |
| COALITIONS                 | Enable coalition handling (see [Coalitions]), default = false.                                                                                                                             |
| ALLOW_PLAYERS_POOL         | Only for [Coalitions]                                                                                                                                                                      |
| COALITION_LOCK_TIME        | The time you are not allowed to change [coalitions] in the format "nn days" or "nn hours". Default is 1 day.                                                                               |
| Coalition Red              | Members of this role are part of the red coalition (see [Coalitions]).                                                                                                                     |
| Coalition Blue             | Members of this role are part of the blue coalition (see [Coalitions]).                                                                                                                    |
| COALITION_BLUE_EVENTS      | Coalition events channel for blue coalition (optional, see [Coalitions]).                                                                                                                  |
| COALITION_BLUE_CHANNEL     | Coalition chat channel for blue coalition (optional, see [Coalitions]).                                                                                                                    |
| COALITION_RED_EVENTS       | Coalition events channel for red coalition (optional, see [Coalitions]).                                                                                                                   |
| COALITION_RED_CHANNEL      | Coalition chat channel for red coalition (optional, see [Coalitions]).                                                                                                                     |

[Coalitions]: coalitions.md
[Userstats]: ../plugins/userstats.md
[Missionstats]: ../plugins/missionstats.md
[DCS_singleton]: https://wiki.hoggitworld.com/view/DCS_singleton_world
