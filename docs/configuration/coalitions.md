---
title: Coalitions
parent: Configuration
nav_order: 3
---
{{ page.path }}
# Coalitions

If you want to support Blue and Red coalitions in your Discord and your DCS servers, you're good to go!
As there are major changes to how the bot behaves with COALITIONS enabled, I decided to have a separate documentation 
about it. It has redundant information in it, which is usually a bad idea, but I thought it might be easier for you
guys to have everything in one place.<br/>
Coalitions are implemented by slot blocking, but can use the recenly added feature of coalition passwords in DCS, too.

{: .note }
> With COALITIONS enabled, some persistent displays will not appear in your server status channels (or will be changed)
> like Player information or Mission Statistics, which would render all the work useless, if you could peek in there and
> see what is going on. You can still use the commands .players or .missionstats in your dedicated coalition channels, but
> you can't see data from the opposite coalition anymore.

The option `COALITION` to handle this feature can be enabled in each server section of `dcsserverbot.ini` individually.
So if you only want to enable strict red/blue handling in one server, you can do that.
Every other server (and their persistent embeds) will not be affected.

---
## Bot Configuration
There are some specific settings for coalitions that you can set in your dcsserverbot.ini:

a) __BOT Section__

| Parameter            | Description                                                                                                                                                                                                                                                                                                                                                                                                          |
|----------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| GREETING_DM          | A greeting message, that people will receive as a DM in Discord, if they join your guild.                                                                                                                                                                                                                                                                                                                            |

A `GREETING_DM` is not mandatory and not linked to coalitions, but it is recommended to tell your new joiners to join a
coalition. I have provided a sample message that you can adapt or use as it is.

b) __ROLES Section__

| Parameter      | Description                                                                                                   |
|----------------|---------------------------------------------------------------------------------------------------------------|
| GameMaster     | Members of this role can run commands that affect the mission behaviour or handle coalition specific details. |

c) __Server Specific Sections__

| Parameter              | Description                                                                                                |
|------------------------|------------------------------------------------------------------------------------------------------------|
| COALITIONS             | Enable coalition handling, default = false.                                                                |                                                                                                                                                                                                                                                                                                                                                 
| COALITION_LOCK_TIME    | The time you are not allowed to change coalitions in the format "nn days" or "nn hours". Default is 1 day. |
| ALLOW_PLAYERS_POOL     | Enable the in-game players pool view (default = false, if coalitions are enabled).                         |
| Coalition Red          | Members of this **role** are part of the red coalition.                                                    |
| Coalition Blue         | Members of this **role** are part of the blue coalition.                                                   |
| COALITION_BLUE_CHANNEL | Coalition channel for blue coalition.                                                                      |
| COALITION_RED_CHANNEL  | Coalition channel for red coalition.                                                                       |

{: .warning }
> Make sure, that all channels for red and blue coalitions have read access **only** for this coalition and not for
> everyone and not for the other coalition! The CHAT-channels for red and blue are similar to the general chat channel,
> but they only replicate chat messages that are being sent to that specific coalition in game.
> Unfortunately, it is not possible to chat back yet, as the DCS API doesn't allow it (or I am too dumb to use it).


## Discord Configuration
The bot uses the following **internal** roles to apply specific permissions to commands.
You can change the role names to the ones being used in your discord. That has to be done in the dcsserverbot.ini 
configuration file.

| Role           | Description                                                                                                                                         |
|----------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| GameMaster     | People with this role can see both coalitions and run specific commands that are helpful in missions.                                               |
| Coalition Blue | People with this role are members of the blue coalition. See Coalitions below for details.                                                          |
| Coalition Red  | People with this role are members of the red coalition. See Coalitions below for details.                                                           |

## Discord Commands
These discord commands are either exclusively for coalition handling like .join and .leave or have been amended for 
coalition use, which means, that the data they display is filtered to data that belongs to your coalition only.

| Command           | Parameter  | Channel                     | Role                   | Description                                                                                              |
|-------------------|------------|-----------------------------|------------------------|----------------------------------------------------------------------------------------------------------|
| .password         | coalition  | admin-channel               | DCS Admin              | Changes the password of a specific coalition on this server.                                             |
| .join             | red / blue | all                         | DCS                    | Joins either Coalition Red or Coalition Blue discord groups.                                             |
| .leave            |            | all                         | DCS                    | Leave the current coalition.                                                                             |
| .players          |            | status-/chat-/admin-channel | DCS                    | Lists the players currently active on the server (for your coalition only!).                             |
| .briefing/.brief  |            | all                         | DCS                    | Shows the description / briefing of the running mission (for your coalition only!).                      |
| .missionstats     |            | status-/chat-/admin-channel | DCS                    | Display the current mission situation for either red or blue and the achievments in kills and captures.  |
