# Coalitions
If you want to support Blue and Red coalitions in your Discord and your DCS servers, you're good to go!
As there are major changes to how the bot behaves with COALITIONS enables, I decided to have a separate documentation 
about it. It has redundant information in it, which is usually a bad idea, but I thought it might be easier for you
guys to have everything in one place.

## Bot Configuration
There are some specific settings for coalitions that you can set in your dcsserverbot.ini:

a) __BOT Section__

| Parameter            | Description                                                                                                                                                                                                                                                                                                                                                                                                          |
|----------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| COALITION_LOCK_TIME  | The time you are not allowed to change coalitions in the format "nn days" or "nn hours". Default is 1 day.                                                                                                                                                                                                                                                                                                           |
| GREETING_DM          | A greeting message, that people will receive as a DM in Discord, if they join your guild.                                                                                                                                                                                                                                                                                                                            |

A GREETING_DM is not mandatory and not linked to coalitions, but it is recommended to tell your new joiners to join a
coalition. I have provided a sample message that you can adapt or use as it is.

b) __ROLES Section__

| Parameter      | Description                                                                                                   |
|----------------|---------------------------------------------------------------------------------------------------------------|
| GameMaster     | Members of this role can run commands that affect the mission behaviour or handle coalition specific details. |
| Coalition Red  | Members of this role are part of the red coalition.                                                           |
| Coalition Blue | Members of this role are part of the blue coalition.                                                          |

c) __Server Specific Sections__

| Parameter                  | Description                                                                                                                                                                                |
|----------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| COALITIONS                 | Enable coalition handling (see "Coalitions" below), default = false.                                                                                                                       |                                                                                                                                                                                                                                                                                                                                                 
| CHAT_CHANNEL_BLUE          | Coalition chat channel for blue coalition (optional, see Coalitions below).                                                                                                                |
| CHAT_CHANNEL_RED           | Coalition chat channel for red coalition (optional, see Coalitions below).                                                                                                                 |

**Attention!** Make sure, that all channels for red and blue coalitions have read access **only** for this coalition
and not for everyone and not for the other coalition!  

## Discord Configuration
The bot uses the following **internal** roles for coalitions to apply specific permissions to commands.
You can change the role names to the ones being used in your discord. That has to be done in the dcsserverbot.ini 
configuration file. If you want to add multiple groups, separate them by comma.

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
