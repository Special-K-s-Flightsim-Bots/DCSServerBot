---
has_children: false
nav_order: 4
---

# Commands
{: .no_toc }

1. TOC
{:toc}

## General Administrative Commands
These commands can be used to administrate the bot itself.

| Command     | Parameter | Channel       | Role    | Description                                                                                                                                                                                                               |
|-------------|-----------|---------------|---------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| .reload     | [Plugin]  | all           | Admin   | Reloads one or all plugin(s) and their configurations from disk.                                                                                                                                                          |
| .upgrade    |           | all           | Admin   | Upgrades the bot to the latest available version (git needed, see below).                                                                                                                                                 |
| .rename     | newname   | admin-channel | Admin   | Renames a DCS server. DCSServerBot auto-detects server renaming, too.                                                                                                                                                     |
| .unregister |           | admin-channel | Admin   | Unregisters the current server from this agent.<br/>Only needed, if the very same server is going to be started on another machine connected to another agent (see "Moving a Server from one Location to Another" below). |

## Plugin Admin

| Command   | Parameter                     | Channel       | Role      | Description                                                                                                             |
|-----------|-------------------------------|---------------|-----------|-------------------------------------------------------------------------------------------------------------------------|
| .update   | [-force]                      | admin-channel | DCS Admin | Updates DCS World to the latest available version. -force can be used, if no update could be detected automatically.    |
| .config   |                               | admin-channel | DCS Admin | Configure name, description, password and num max players for your server.                                              |
| .password | [coalition]*                  | admin-channel | DCS Admin | Changes the password of a DCS server or a specific coalition* on this server.                                           |
| .kick     | name [reason]                 | admin-channel | DCS Admin | Kicks the user with the in-game name "name" from the DCS server. The "reason" will be provided to the user.             |
| .spec     | name [reason]                 | admin-channel | DCS Admin | Moves the user with the in-game name "name" to spectators. The "reason" will be provided to the user as a chat message. |
| .ban      | @member/ucid [days] [reason]  | all           | DCS Admin | Bans a specific player either by their Discord ID or UCID for the given amount of days (optional).                      |
| .unban    | @member/ucid                  | all           | DCS Admin | Unbans a specific player either by their Discord ID or UCID.                                                            |
| .bans     |                               | all           | DCS Admin | Lists the current active bans.                                                                                          |
| .download |                               | admin-channel | DCS Admin | Download a dcs.log, dcsserverbot.log, bot config file or a mission into a DM, path or configured channel.               |
| .shell    |                               | admin-channel | Admin     | Runs a shell command on a specific node.                                                                                |

## Plugin Cloud

| Command               | Parameter        | Role      | Description                                          |
|-----------------------|------------------|-----------|------------------------------------------------------|
| .resync               | [@member / ucid] | DCS Admin | Resyncs all players (or this player) with the cloud. |
| .cloudstats / .cstats | [@member / ucid] | DCS       | Display player cloud statistics (overall, per guild) |

## Plugin CreditSystem

| Command  | Parameter          | Role | Description                                           |
|----------|--------------------|------|-------------------------------------------------------|
| .credits |                    | DCS  | Displays the players campaign credits.                |
| .donate  | <@member> <points> | DCS  | Donate any of your campaign points to another member. |

## Plugin DBExporter

| Command | Parameter | Channel | Role    | Description                                            |
|---------|-----------|---------|---------|--------------------------------------------------------|
| .export |           | all     | Admin   | Exports the whole database. Table filters don't apply! |

## Plugin GameMaster

| Command           | Parameter                             | Channel             | Roles                 | Description                                                                                                                      |
|-------------------|---------------------------------------|---------------------|-----------------------|----------------------------------------------------------------------------------------------------------------------------------|
| .chat             | message                               | chat-/admin-channel | DCS                   | Send a message to the DCS in-game-chat.                                                                                          |
| .popup            | red/blue/all/player [timeout] message | admin-channel       | DCS Admin, GameMaster | Send a popup to the dedicated coalition or player* in game with an optional timeout.                                             |
| .flag             | name [value]                          | admin-channel       | DCS Admin, GameMaster | Sets (or clears) a flag inside the running mission or returns the current value.                                                 |
| .variable         | name [value]                          | admin-channel       | DCS Admin, GameMaster | Sets (or gets) a mission variable.                                                                                               |
| .do_script        | lua code                              | admin-channel       | DCS Admin, GameMaster | Run specific lua code inside the running mission.                                                                                |
| .do_script_file   | file                                  | admin-channel       | DCS Admin, GameMaster | Load a script (relative to Saved Games\DCS...) into the running mission.                                                         |
| .reset_coalitions |                                       | all                 | DCS Admin             | Resets all user-coalition-bindings on all servers.                                                                               |
| .campaign         | add <name> [start] [stop]             | admin-channel       | DCS Admin, GameMaster | Creates a new campaign "name", starting at "start" and ending at "stop". start / stop should be in format YYYYMMDD or DDMMYYYY.  |
| .campaign         | start <name>                          | admin-channel       | DCS Admin, GameMaster | Starts a new campaign with the provided name, if none is running.                                                                |
| .campaign         | stop                                  | admin-channel       | DCS Admin, GameMaster | Stops the current campaign.                                                                                                      |
| .campaign         | delete [name]                         | admin-channel       | DCS Admin, GameMaster | Deletes a campaign out of the list. If no name is provided the current campaign will be deleted.                                 |
| .campaign         | list [-all]                           | admin-channel       | DCS Admin, GameMaster | Lists all available campaigns. If "-all" is not provided (default), only campaigns from now on will be displayed.                |

## Plugin Greenieboard

| Command         | Parameter          | Channel       | Role      | Description                                                                                         |
|-----------------|--------------------|---------------|-----------|-----------------------------------------------------------------------------------------------------|
| .greenieboard   | rows               | all           | DCS       | Print the current greenieboard (per server). 10 rows is default, can be changed with the parameter. |
| .carrier        | @member / DCS name | all           | DCS       | Display the last carrier landings for this user and a detailed view on selection.                   |

## Plugin Mission

| Command          | Parameter                | Channel                     | Role                  | Description                                                                                                              |
|------------------|--------------------------|-----------------------------|-----------------------|--------------------------------------------------------------------------------------------------------------------------|
| .servers         |                          | all                         | DCS                   | Lists all registered DCS servers and their status (same as .mission but for all). Servers will auto-register on startup. |
| .mission         |                          | status-/admin-channel       | DCS Admin             | Information about the active mission. Persistent display in status-channel.                                              |
| .players         |                          | status-/chat-/admin-channel | DCS                   | Lists the players currently active on the server. Persistent display in status-channel.                                  |
| .afk             | [minutes]                | all                         | DCS Admin             | Lists players that sit on Spectators since more than [minutes] (default 10 mins).                                        |
| .list / .load    |                          | admin-channel               | DCS Admin             | Select a mission to start / restart.                                                                                     |
| .add             | [miz-file]               | admin-channel               | DCS Admin             | Select a mission from the file system to be added to the mission list.                                                   |
| .delete / .del   |                          | admin-channel               | DCS Admin             | Delete a mission from the mission list and optional from the file system.                                                |
| .restart         | [time in secs] [message] | admin-channel               | DCS Admin             | Restarts the current mission after [time] seconds. A message will be sent as a popup to that server.                     |
| .pause           |                          | admin-channel               | DCS Admin, GameMaster | Pauses the current running mission.                                                                                      |
| .unpause         |                          | admin-channel               | DCS Admin, GameMaster | Resumes the current running mission.                                                                                     |
| .briefing/.brief |                          | all                         | DCS                   | Shows the description / briefing of the running mission.                                                                 |
| .atis / .weather | Airport Name             | all                         | DCS                   | Information about a specific airport in this mission (incl. weather).                                                    |

## Plugin MissionStats

| Command                   | Parameter                        | Channel                     | Role | Description                                                                                         |
|---------------------------|----------------------------------|-----------------------------|------|-----------------------------------------------------------------------------------------------------|
| .missionstats             |                                  | status-/chat-/admin-channel | DCS  | Display the current mission situation for red and blue and the achievements in kills and captures.  |
| .sorties                  | [@member / Player Name] [period] | all                         | DCS  | Display the number of sorties and real flight time per module / period.                             |
| .modulestats / .modstats  | [@member / Player Name] [period] | all                         | DCS  | Display module and weapon statistics per module.                                                    |
| .refuelings / .refuel     | [@member / Player Name] [period] | all                         | DCS  | Display refuelings per module.                                                                      |

## Plugin ModManager

| Command         | Parameter                | Channel       | Role             | Description                                                                     |
|-----------------|--------------------------|---------------|------------------|---------------------------------------------------------------------------------|
| .packages       |                          | admin-channel | Admin, DCS Admin | Lists all installed packages of this server and lets you update or remove them. |
| .add_package    |                          | admin-channel | Admin            | Installs a specific package.                                                    |

## Plugin Punishment

| Command  | Parameter                     | Channel | Role      | Description                                            |
|----------|-------------------------------|---------|-----------|--------------------------------------------------------|
| .forgive | \<member>/\<ucid>             | all     | DCS Admin | Deletes all punishment points for this member / user.  |
| .penalty | [member] / [ucid]             | all     | DCS       | Displays the players penalty points.                   |
| .punish  | \<member> / \<ucid> \<points> | Admin   | DCS Admin | Add punishment points to a user.                       |

## Plugin Scheduler

| Command      | Parameter | Channel       | Role      | Description                                                                                                                                    |
|--------------|-----------|---------------|-----------|------------------------------------------------------------------------------------------------------------------------------------------------|
| .startup     |           | admin-channel | DCS Admin | Starts a dedicated DCS server process.                                                                                                         |
| .shutdown    | [-force]  | admin-channel | DCS Admin | Shuts the dedicated DCS server process down.<br/>If `-force` is used, no player check will be executed and no onShutdown command will be run.  |
| .start       |           | admin-channel | DCS Admin | Starts a stopped DCS server.                                                                                                                   |
| .stop        |           | admin-channel | DCS Admin | Stops a DCS server.                                                                                                                            |
| .status      |           | all           | DCS       | Shows the status of all configured DCS servers.                                                                                                |
| .maintenance |           | admin-channel | DCS Admin | Sets the servers maintenance mode.                                                                                                             |
| .clear       |           | admin-channel | DCS Admin | Clears the maintenance state of a server.                                                                                                      |
| .preset      |           | admin-channel | DCS Admin | Changes the preset (date/time/weather) of a mission. Multiple selections will apply all presets at once.                                       |
| .reset       |           | admin-channel | DCS Admin | Calls a configurable reset command.                                                                                                            |

## Plugin ServerStats

| Command      | Parameter               | Role  | Description                                                                                                                                             |
|--------------|-------------------------|-------|---------------------------------------------------------------------------------------------------------------------------------------------------------|
| .serverstats | [day/week/month] [-all] | Admin | Displays server statistics, like usual playtime, most frequented servers and missions.<br/>If -all is provided, you can cycle through all your servers. |
| .serverload  | [hour/day/week] [-all]  | Admin | Displays technical server statistics, like CPU load, memory consumption, etc.<br/>If -all is provided, you can cycle through all your server nodes.     |

## Plugin UserStats

| Command                | Parameter                                 | Channel | Role           | Description                                                                                           |
|------------------------|-------------------------------------------|---------|----------------|-------------------------------------------------------------------------------------------------------|
| .statistics/.stats     | [@member / DCS name] [day/week/month/all] | all     | DCS            | Display your own statistics or that of a specific member.                                             |
| .statsme               | [day/week/month/all]                      | all     | DCS            | Send your own statistics in a DM instead of displaying them in public.                                |
| .highscore/.hs         | [day/week/month/all]                      | all     | DCS            | Shows the players with the most playtime or most kills in specific areas (CAP/CAS/SEAD/Anti-Ship)     |
| .link                  | @member ucid                              | all     | DCS Admin      | Sometimes users can't be linked automatically. That is a manual workaround.                           |
| .unlink                | @member / ucid                            | all     | DCS Admin      | Unlink a member from a ucid / ucid from a user, if the automatic linking didn't work.                 |
| .info                  | @member / ucid / DCS name                 | all     | DCS Admin      | Displays information about that user and let you (un)ban, kick or unlink them.                        |
| .linkcheck             |                                           | all     | DCS Admin      | Checks if a DCS user could be matched to a member.                                                    |
| .mislinks / .mislinked |                                           | all     | DCS Admin      | Checks if a DCS user is possibly mismatched with the wrong member (might still be correct though!).   |
| .delete_statistics     | [@member]                                 | all     | DCS, DCS Admin | Deletes the users statistics. DCS Admin can delete for other users.                                   |
| .reset_statistics      |                                           | all     | Admin          | Resets the statistics for this server.                                                                |
| .linkme                |                                           | all     | DCS            | Link a discord user to a DCS user (user self-service).                                                |

## Changing Commands
If you want to change the name, aliases or permissions of an existing command or you want to disable it, you can do that
by adding a "commands"-section to your "plugin".json configuration file.

Example (mission.json):
```json
{
  "commands": {
    "player": {
      "name": "jugadores",
      "commands": {
        "bans": {
          "roles": [
            "Admin"
          ],
          "name": "prohibiciones",
          "description": "mostrar una lista de todas las prohibiciones en sus servidores"
        },
        "afk": {
          "enabled": false
        }
      }
    }
  }
}
```
This changes the group name of "player" to its spanish name "jugadores", the bans sub-command to "prohibiciones" and
gives it some documentation. Only users belonging to the Admin group are allowed to run this command. The 2nd example
disabled the afk sub-command at all. So nobody can see of use it anymore.
