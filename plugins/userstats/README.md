# Plugin "Userstats"
DCSServerBot comes with a built in, database driven statistics system. It allows either users to show their own achievements like k/d-ratio, flighttimes per module, server or map, etc.
For server owners, it allows you to see which of your servers and missions are being used most, at which time and from which kind of users (Discord members vs. public players).

## Configuration
User statistics can be enabled or disabled in the server configuration (see [e) Server Specific Sections](../../README.md)).
Userstats needs the Mission plugin to be loaded first.

## User Linking
It is recommended that your users link their Discord ID to their UCID (DCS World ID). The bot can try to do that by 
itself (AUTOMATCH = true), but might fail, especially, when the ingame names and Discord names of users differ a lot.
If the AUTOMATCH is disabled or was not successful, users can generate a unique TOKEN that is being sent as a DM with 
```.linkme```. The TOKEN can then be entered in the in-game chat as a chat-command with ```-linkme TOKEN```.

## Discord Commands

| Command                | Parameter                             | Channel | Role      | Description                                                                                         |
|------------------------|---------------------------------------|---------|-----------|-----------------------------------------------------------------------------------------------------|
| .statistics/.stats     | [@member / DCS name] [day/week/month] | all     | DCS       | Display your own statistics or that of a specific member.                                           |
| .highscore/.hs         | [day/week/month]                      | all     | DCS       | Shows the players with the most playtime or most kills in specific areas (CAP/CAS/SEAD/Anti-Ship)   |
| .link                  | @member ucid                          | all     | DCS Admin | Sometimes users can't be linked automatically. That is a manual workaround.                         |
| .unlink                | @member / ucid                        | all     | DCS Admin | Unlink a member from a ucid / ucid from a user, if the automatic linking didn't work.               |
| .info                  | @member / ucid / DCS name             | all     | DCS Admin | Displays information about that user and let you (un)ban, kick or unlink them.                      |  
| .linkcheck             |                                       | all     | DCS Admin | Checks if a DCS user could be matched to a member.                                                  |
| .mislinks / .mislinked |                                       | all     | DCS Admin | Checks if a DCS user is possibly mismatched with the wrong member (might still be correct though!). |
| .reset                 |                                       | all     | Admin     | Attention: Resets the statistics for this server.                                                   |
| .linkme                |                                       | all     | DCS       | Link a discord user to a DCS user (user self-service).                                              |

## How to disable Userstats inside of missions
Sometimes you don't want your mission to generate per-user statistics, but you don't want to configure your server to disable them forever?
Well, then - just disable them from inside your mission:
```lua
  dofile(lfs.writedir() .. 'Scripts/net/DCSServerBot/DCSServerBot.lua')
  dcsbot.disableUserStats()
```
