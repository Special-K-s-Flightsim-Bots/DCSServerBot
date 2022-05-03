# Plugin "Mission"
The mission plugin adds commands for amending the mission list, scheduled restarts, persistent mission- and player-embeds to be displayed in your status channels and ATIS like information for the missions' airports. 

## Discord Commands

| Command          | Parameter                | Channel                     | Role                  | Description                                                                                                                                                                                                                                          |
|------------------|--------------------------|-----------------------------|-----------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| .mission         |                          | status-/admin-channel       | DCS Admin             | Information about the active mission. Persistent display in status-channel.                                                                                                                                                                          |
| .players         |                          | status-/chat-/admin-channel | DCS                   | Lists the players currently active on the server. Persistent display in status-channel.                                                                                                                                                              |
| .list / .load    | [number]                 | admin-channel               | DCS Admin             | Lists all available missions on this server and let you start or restart one of them.                                                                                                                                                                |
| .add             | [miz-file]               | admin-channel               | DCS Admin             | Adds a specific mission to the list of missions, that has to be in Saved Games/DCS[.OpenBeta]/Missions. If no miz file is provided, a list of all available files in the servers Missions directory (no subdirs supported by now!) will be provided. |
| .delete / .del   |                          | admin-channel               | DCS Admin             | Lists all available missions on this server and let you delete one of them.                                                                                                                                                                          |
| .restart         | [time in secs] [message] | admin-channel               | DCS Admin             | Restarts the current mission after [time] seconds. A message will be sent as a popup to that server.                                                                                                                                                 |
| .pause           |                          | admin-channel               | DCS Admin, GameMaster | Pauses the current running mission.                                                                                                                                                                                                                  |
| .unpause         |                          | admin-channel               | DCS Admin, GameMaster | Resumes the current running mission.                                                                                                                                                                                                                 |
| .briefing/.brief |                          | all                         | DCS                   | Shows the description / briefing of the running mission.                                                                                                                                                                                             |
| .atis / .weather | Airport Name             | all                         | DCS                   | Information about a specific airport in this mission (incl. weather).                                                                                                                                                                                |

## Tables
### Players
| Column          | Type                  | Description                                              |
|-----------------|-----------------------|----------------------------------------------------------|
| #ucid           | TEXT                  | Unique ID of this user (DCS ID).                         |
| discord_id      | BIGINT                | Discord ID of this user (if matched) or -1 otherwise.    |
| name            | TEXT                  | Last used DCS ingame-name of this user.                  |
| ipaddr          | TEXT                  | Last used IP-address of this user.                       |
| manual          | BOOLEAN DEFAULT FALSE | True if this user was manually matched, FALSE otherwise. |
| coalition       | TEXT                  | The coalition the user belongs to.                       |
| coalition_leave | TIMESTAMP             | The time that user last left their coalition.            |
| last_seen       | TIMESTAMP             | Time the user was last seen on the DCS servers.          |

### Missions
| Column          | Type               | Description                                     |
|-----------------|--------------------|-------------------------------------------------|
| #id             | SERIAL             | Auto-incrementing unique mission ID.            |
| server_name     | TEXT NOT NULL      | The name of the DCS server this mission was on. |
| mission_name    | TEXT NOT NULL      | The name of the mission.                        |
| mission_theatre | TEXT NOT NULL      | The map being used by the mission.              |
| mission_start   | TIMESTAMP NOT NULL | When was this mission started.                  |
| mission_end     | TIMESTAMP          | When was this mission stopped.                  |
