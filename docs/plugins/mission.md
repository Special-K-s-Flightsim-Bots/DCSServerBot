---
title: Mission
parent: Plugin System
nav_order: 0
---

# Plugin "Mission"

The mission plugin adds commands for amending the mission list, persistent mission- and player-embeds to be displayed 
in your status channels and ATIS like information for the missions' airports. 

## Uploading of Missions

You can upload .miz files in the configured admin channel of your server(s). You need the DCS Admin role for that.
Existing missions will be replaced (with security question) and if the server is running with that mission, it will be
restarted (another security question will apply). Newly added missions will be auto-added to the mission list.

## Auto-Scanning

If you set _AUTOSCAN = true_ n your BOT section of dcsserverbot.ini, all miz files that were added into the Missions
folder of your DCS-server (for instance via a Google Drive) will be auto-added to your mission list.

## Discord Commands

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

## Database Tables

- [PLAYERS](../database.md#players)
- [PLAYERS_HIST](../database.md#players_hist)
- [MISSIONS](../database.md#missions)
