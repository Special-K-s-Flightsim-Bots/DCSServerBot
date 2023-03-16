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

[Coalitions]: coalitions.md
