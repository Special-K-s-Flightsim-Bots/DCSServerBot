# Plugin "Userstats"
DCSServerBot comes with a built in, database driven statistics system. It allows either users to show their own achievements like k/d-ratio, flighttimes per module, server or map, etc.
For server owners, it allows you to see which of your servers and missions are being used most, at which time and from which kind of users (Discord members vs. public players).

## Configuration
User statistics can be enabled or disabled in the server configuration (see [e) Server Specific Sections](../../README.md)).
Userstats needs the Mission plugin to be loaded first.

## Discord Commands

| Command            | Parameter                             | Role      | Description                                                                                       |
|--------------------|---------------------------------------|-----------|---------------------------------------------------------------------------------------------------|
| .statistics/.stats | [@member / DCS name] [day/week/month] | DCS       | Display your own statistics or that of a specific member.                                         |
| .highscore/.hs     | [day/week/month]                      | DCS       | Shows the players with the most playtime or most kills in specific areas (CAP/CAS/SEAD/Anti-Ship) |
| .link              | @member ucid                          | DCS Admin | Sometimes users can't be linked automatically. That is a manual workaround.                       |
| .unlink            | @member / ucid                        | DCS Admin | Unlink a member from a ucid / ucid from a user, if the automatic linking didn't work.             |
 | .info              | @member / ucid / DCS name             | DCS Admin | Displays information about that user and let you (un)ban, kick or unlink them.                    |  
 | .linkcheck         |                                       | DCS Admin | Checks all member : ucid links and let them be fixed.                                             |
| .reset             |                                       | Admin     | Attention: Resets the statistics for this server.                                                 |

## How to disable Userstats inside of missions
Sometimes you don't want your mission to generate per-user statistics, but you don't want to configure your server to disable them forever?
Well, then - just disable them from inside your mission:
```lua
  dofile(lfs.writedir() .. 'Scripts/net/DCSServerBot/DCSServerBot.lua')
  dcsbot.disableUserStats()
```
