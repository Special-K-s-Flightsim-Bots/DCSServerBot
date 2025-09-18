# Plugin "Mission"
The mission plugin adds commands for amending the mission list, persistent mission- and player-embeds to be displayed 
in your status channels and ATIS like information for the missions' airports. 

## User Linking
It is recommended that your users link their Discord ID to their UCID (DCS World ID). The bot can try to do that by 
itself (bot.yaml: `automatch: true`), but might fail, especially, when the in-game names and Discord names of users 
differ a lot.
> [!NOTE]
> Users can generate a unique TOKEN that is being sent as a DM with the ```/linkme``` command.<br>
> The TOKEN can then be entered in the in-game chat as a chat-command with ```-linkme TOKEN```.

## Uploading of Missions
You can upload .miz files in the configured admin channel of your server(s). Existing missions will be replaced (with 
security question) and if the server is running with that mission, it will be restarted (another security question 
will apply). Newly added missions will be auto-added to the mission list.<br>

## Custom User Menus
DCSServerBot allows you to create custom user menus, that people can use via the F10 menu. The default usecase is to
call chat commands with them.<br>
To configure the menu, you need to create a file "config/menus.yaml" in your configuration directory like so:
```yaml
DEFAULT:
  - DCSServerBot:                   # This is the name of the F10-Root menu (multiple possible)
      - Help:                       # Top-Level command below the root menu
          command: onChatCommand
          subcommand: help          # Call the "help" chat-command
      - GameMaster:                 # Sub-menu section
        - Launch AWACS:             # Command inside of the subsection
            command: onChatCommand
            subcommand: flag        # Call the chat-command "flag 1 1", which sets flag 1 to value 1
            params: [ 1, 1 ]
        - Disable Punishments:      # Call a game event "disablePunishments"
            command: disablePunishments
            discord:                # This menu option will only be available for the DCS Admin role
              - DCS Admin
        - Start Campaign:
            command: startCampaign  # Call a game event "startCampaign" 
            ucid:                   # This command will only be available for this UCID
              - aabbccddeeffgghhiijjkkllmmnnoopp
        - Stop Campaign:
            command: stopCampaign
            ucid:
              - aabbccddeeffgghhiijjkkllmmnnoopp
      - Weather:
        - Morning:
            command: onChatCommand
            subcommand: preset      # Select the preset "Morning" (needs to exist!)
            params: [ 'Morning' ]
        - Night:
            command: onChatCommand
            subcommand: preset      # Select the preset "Night" (needs to exist!)
            params: [ 'Night' ]
        - RealWeather:
            command: onChatCommand
            subcommand: realweather # Run "DCS RealWeather" with a specified airport
            params: ['UGKO']
```
> [!NOTE]
> DCS World can only create menus for groups. This means, that you should create groups that contain single units only,
> especially, if you use commands that can only be called by specific roles.
> 
> Roles are checked twice though: once, when the menu gets created. You should not see any command, that your role can
> not use. A second check is done, when the command is being executed (onChatCommand only).

## Configuration
You can configure the behaviour of the mission plugin with an optional config/plugins/mission.yaml:
```yaml
DEFAULT:
  event_filter:       # do NOT report these events (default: [])
    - connect
    - disconnect
    - change_slot
    - friendly_fire
    - self_kill
    - kill
    - takeoff
    - landing
    - crash
    - eject
    - pilot_death
    - shot
    - hit
  uploads:                      # Configure how mission uploads are handled
    enabled: true               # Here you can disable the feature at all (default: true = enabled)
    channel: 112233445566778899 # Optional: mission upload channel (default: admin channel)
    discord:
      - DCS Admin               # Define which roles are allowed to upload missions (default: DCS Admin)
```

## Auto-Scanning
If you set `autoscan: true` in your server configuration of your servers.yaml, all miz files that were added into the 
Missions folder of your DCS-server (for instance via a Google Drive) will be auto-added to your mission list.

## Discord Commands
| Command                | Parameter                                 | Channel               | Role                  | Description                                                                                                                                                                                                                       |
|------------------------|-------------------------------------------|-----------------------|-----------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| /linkme                |                                           | all                   | DCS                   | Link a discord user to a DCS user (user self-service).                                                                                                                                                                            |
| /link                  | @member player                            | all                   | DCS Admin             | Sometimes users can't be linked automatically. This is the manual workaround.                                                                                                                                                     |
| /unlink                | user                                      | all                   | DCS Admin             | Unlink a member from a ucid / ucid from a user, if the automatic linking made a mistake.                                                                                                                                          |
| /linkcheck             |                                           | all                   | DCS Admin             | Checks if a DCS user could be matched to a member.                                                                                                                                                                                |
| /mislinks              |                                           | all                   | DCS Admin             | Checks if a DCS user is possibly mismatched with the wrong member (might still be correct though!).                                                                                                                               |
| /convert               | mode [lat] [lon] [mgrs]                   | all                   | DCS                   | Convert LAT/LON to MGRS or vice versa.                                                                                                                                                                                            |
| /mission info          |                                           | status-/admin-channel | DCS                   | Information about the active mission. Persistent display in status-channel.                                                                                                                                                       |
| /mission manage        |                                           | admin-channel         | DCS Admin             | Start/stop/load mission in a nice Discord view.                                                                                                                                                                                   |
| /mission load          | [run_extensions]                          | admin-channel         | DCS Admin             | Select a mission to start / restart. run_extensions means, if mission modifications like RealWeather or MizEdit should be processed (default: yes)                                                                                |
| /mission add           | mission [autostart]                       | admin-channel         | DCS Admin             | Select a mission from the file system to be added to the mission list. Autostart puts it on the loadindex position (default: false).                                                                                              |
| /mission delete        | mission                                   | admin-channel         | DCS Admin             | Delete a mission from the mission list and optional from the file system.                                                                                                                                                         |
| /mission restart       | [delay] [reason] [run_extensions]         | admin-channel         | DCS Admin             | Restarts the current mission after [delay] seconds (default: 120). A message will be sent as a popup to that server.                                                                                                              |
| /mission rotate        | [delay] [reason] [run_extensions]         | admin-channel         | DCS Admin             | Rotate to the next mission after [delay] seconds (default: 120). A message will be sent as a popup to that server.                                                                                                                |
| /mission pause         |                                           | admin-channel         | DCS Admin, GameMaster | Pauses the current running mission.                                                                                                                                                                                               |
| /mission unpause       |                                           | admin-channel         | DCS Admin, GameMaster | Resumes the current running mission.                                                                                                                                                                                              |
| /mission briefing      |                                           | all                   | DCS                   | Shows the description / briefing of the running mission.                                                                                                                                                                          |
| /mission atis          | airport                                   | all                   | DCS                   | Information about a specific airport in this mission (incl. weather).                                                                                                                                                             |
| /mission modify        | [presets_file] [use_orig] [simulate_only] | admin-channel         | DCS Admin             | Modify the mission with a specific [preset](../../extensions/mizedit/README.md). You can provide an optional presets file (default: presets.yaml) and select if you want to change the running mission or based on the orig file. |
| /mission save_preset   | name                                      | admin-channel         | DCS Admin             | Save the current missions weather as a new preset.                                                                                                                                                                                |
| /mission persistence   | name                                      | admin-channel         | DCS Admin             | Enabled persistence for the respectice mission (renames it to .sav). To roll it back, use /mission rollback.                                                                                                                      |
| /mission rollback      | name                                      | admin-channel         | DCS Admin             | Rollback a mission to the original, unmodified version.                                                                                                                                                                           |
| /mission fog           | [thickness] [visibility]                  | admin-channel         | DCS Admin             | DCS 2.9.10+: Changes the thickness and/or visibility of fog. When called without a parameter, it will return the current fog in the mission.                                                                                      |
| /mission fog_animation | [presets_file]                            | admin-channel         | DCS Admin             | DCS 2.9.10+: Select a fog animation from a presets file and applies it to the running mission.                                                                                                                                    |
| /info                  | member/player                             | all                   | DCS Admin             | Displays information about that user and let you (un)ban, kick or unlink them.                                                                                                                                                    |  
| /player info           | server player                             | all                   | DCS                   | Displays information about this player (incl. radios, if SRS is active).                                                                                                                                                          |
| /player find or /find  | name                                      | all                   | DCS Admin             | Finds players by name (incl. historic join data).                                                                                                                                                                                 |
| /player list           |                                           | all                   | DCS                   | Lists the players currently active on the server. Persistent display in status-channel.                                                                                                                                           |
| /player spec           | server player [reason]                    | admin-channel         | DCS Admin             | Moves the respective player to a spectator slot.                                                                                                                                                                                  |
| /player kick           | server player [reason]                    | admin-channel         | DCS Admin             | Kicks the respective player from the DCS server.                                                                                                                                                                                  |
| /player ban            | server player                             | admin-channel         | DCS Admin             | (Optional: temporarily) bans the respective player from all DCS servers.                                                                                                                                                          |
| /player lock           | server player                             | admin-channel         | DCS Admin             | Lock a player to a specific slot. They can go back to spectators, but can not use any other seat until unlocked.                                                                                                                  |
| /player unlock         | server player                             | admin-channel         | DCS Admin             | Frees the player again.                                                                                                                                                                                                           |
| /player afk            | server [minutes]                          | all                   | DCS Admin             | Lists players that sit on Spectators since more than [minutes] (default 10 mins).                                                                                                                                                 |
| /player exempt         | player server                             | all                   | DCS Admin             | Puts a player onto the AFK-exemptions list. They will not be kicked for being afk anymore.                                                                                                                                        |
| /player inactive       | period number                             | admin-channel         | DCS Admin             | Show users that are inactive for a specific amount of time.                                                                                                                                                                       |
| /player chat           | server player message                     | admin-channel         | DCS Admin, GameMaster | Sends a in-game chat message to the respective player.                                                                                                                                                                            |
| /player popup          | server player message                     | admin-channel         | DCS Admin, GameMaster | Sends a popup message to the respective player.                                                                                                                                                                                   |
| /player screenshot     | server player                             | admin-channel         | DCS Admin             | Creates a screenshot of the users DCS screen and displays it (needs to be enabled in the server).                                                                                                                                 |
| /player compare        | player1 player2                           | admin-channel         | DCS Admin             | Compare two players to see if they are the same guy (better detect imposters).                                                                                                                                                    |
| /watch add             | player reason                             | admin-channel         | DCS Admin             | Puts a player on the watchlist. Everytime they join, it will be reported to DCS Admin.                                                                                                                                            |
| /watch delete          | player                                    | admin-channel         | DCS Admin             | Removes a user from the watchlist.                                                                                                                                                                                                |
| /watch list            |                                           | admin-channel         | DCS Admin             | Lists all active watches.                                                                                                                                                                                                         |

## In-Game Chat Commands
| Command  | Parameter     | Role      | Description                                                                      |
|----------|---------------|-----------|----------------------------------------------------------------------------------|
| -linkme  | token         | all       | Links your DCS user to a Discord user (/linkme in Discord has to be used first). |
| -911     | message       | all       | Send a message to the DCS Admin role.                                            |
| -atis    | airport       | all       | Shows the respective airports ATIS information.                                  |
| -restart | time          | DCS Admin | Restarts the current mission after [time] seconds.                               |
| -list    |               | DCS Admin | Lists missions available to .load                                                |
| -load    | num           | DCS Admin | Loads the mission number `num` from the mission `.list`                          |
| -ban     | name [reason] | DCS Admin | Ban player `name` with reason [reason] from **all** DCS servers.                 |
| -kick    | name [reason] | DCS Admin | Kicks player `name` with reason [reason].                                        |
| -spec    | name [reason] | DCS Admin | Moves player `name` back to spectators.                                          |
| -preset  | [preset id]   | DCS Admin | List or loads the respective preset.                                             |

## Tables
### Players
| Column          | Type                  | Description                                              |
|-----------------|-----------------------|----------------------------------------------------------|
| #ucid           | TEXT                  | Unique ID of this user (DCS ID).                         |
| discord_id      | BIGINT                | Discord ID of this user (if matched) or -1 otherwise.    |
| name            | TEXT                  | Last used DCS in-game-name of this user.                 |
| ipaddr          | TEXT                  | Last used IP-address of this user.                       |
| coalition       | TEXT                  | The coalition the user belongs to.                       |
| coalition_leave | TIMESTAMP             | The time that user last left their coalition.            |
| manual          | BOOLEAN DEFAULT FALSE | True if this user was manually matched, FALSE otherwise. |
| last_seen       | TIMESTAMP             | Time the user was last seen on the DCS servers.          |

### Players_Hist
This table keeps a history of all changes to the main player table.

| Column     | Type                    | Description                                              |
|------------|-------------------------|----------------------------------------------------------|
| #id        | NUMBER                  | Unique ID (sequence)                                     |
| ucid       | TEXT                    | Unique ID of this user (DCS ID).                         |
| discord_id | BIGINT                  | Discord ID of this user (if matched) or -1 otherwise.    |
| name       | TEXT                    | Last used DCS in-game-name of this user.                 |
| ipaddr     | TEXT                    | Last used IP-address of this user.                       |
| coalition  | TEXT                    | The coalition the user belongs to.                       |
| manual     | BOOLEAN                 | True if this user was manually matched, FALSE otherwise. |
| time       | TIMESTAMP DEFAULT NOW() | Time of the change.                                      |

### Missions
| Column          | Type               | Description                                     |
|-----------------|--------------------|-------------------------------------------------|
| #id             | SERIAL             | Auto-incrementing unique mission ID.            |
| server_name     | TEXT NOT NULL      | The name of the DCS server this mission was on. |
| mission_name    | TEXT NOT NULL      | The name of the mission.                        |
| mission_theatre | TEXT NOT NULL      | The map being used by the mission.              |
| mission_start   | TIMESTAMP NOT NULL | When was this mission started.                  |
| mission_end     | TIMESTAMP          | When was this mission stopped.                  |
