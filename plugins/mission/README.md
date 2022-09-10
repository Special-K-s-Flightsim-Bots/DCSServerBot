# Plugin "Mission"
The mission plugin adds commands for amending the mission list, scheduled restarts, persistent mission- and player-embeds to be displayed in your status channels and ATIS like information for the missions' airports. 

## Uploading of Missions
You can upload .miz files in the configured admin channel of your server(s). You need the DCS Admin role for that.
Existing missions will be replaced (with security question) and if the server is running with that mission, it will be
restarted (another security question will apply). Newly added missions will be auto-added to the mission list.

## Discord Commands

| Command          | Parameter                | Channel                     | Role                  | Description                                                                                          |
|------------------|--------------------------|-----------------------------|-----------------------|------------------------------------------------------------------------------------------------------|
| .mission         |                          | status-/admin-channel       | DCS Admin             | Information about the active mission. Persistent display in status-channel.                          |
| .players         |                          | status-/chat-/admin-channel | DCS                   | Lists the players currently active on the server. Persistent display in status-channel.              |
| .list / .load    |                          | admin-channel               | DCS Admin             | Select a mission to start / restart.                                                                 |
| .add             | [miz-file]               | admin-channel               | DCS Admin             | Select a mission from the file system to be added to the mission list.                               |
| .delete / .del   |                          | admin-channel               | DCS Admin             | Delete a mission from the mission list and optional from the file system.                            |
| .restart         | [time in secs] [message] | admin-channel               | DCS Admin             | Restarts the current mission after [time] seconds. A message will be sent as a popup to that server. |
| .pause           |                          | admin-channel               | DCS Admin, GameMaster | Pauses the current running mission.                                                                  |
| .unpause         |                          | admin-channel               | DCS Admin, GameMaster | Resumes the current running mission.                                                                 |
| .briefing/.brief |                          | all                         | DCS                   | Shows the description / briefing of the running mission.                                             |
| .atis / .weather | Airport Name             | all                         | DCS                   | Information about a specific airport in this mission (incl. weather).                                |

## Tables
### Players
| Column          | Type                  | Description                                              |
|-----------------|-----------------------|----------------------------------------------------------|
| #ucid           | TEXT                  | Unique ID of this user (DCS ID).                         |
| discord_id      | BIGINT                | Discord ID of this user (if matched) or -1 otherwise.    |
| name            | TEXT                  | Last used DCS ingame-name of this user.                  |
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
| name       | TEXT                    | Last used DCS ingame-name of this user.                  |
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
