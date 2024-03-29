---
title: UserStats
parent: Plugin System
nav_order: 0
---

# Plugin "UserStats"

DCSServerBot comes with a built-in, database driven statistics system. It allows either users to show their own achievements like k/d-ratio, flighttimes per module, server or map, etc.
For server owners, it allows you to see which of your servers and missions are being used most, at which time and from which kind of users (Discord members vs. public players).

## Configuration
To use the persistent highscore, you need to add a file named userstats.json to your config directory:
```json
{
  "configs": [
    {
      "highscore": {
        "channel": 956901848281931123,
        "params": {
          "period": "month",
          "limit": 10
        }
      }
    },
    {
      "installation": "DCS.release_server",
      "highscore": {
        "channel": 826772687216181456,
        "params": {
          "period": "campaign:mycampaign",
          "limit": 5
        }
      }
    }
  ]
}
```
More highscores per server are supported, if you provide a list instead:
```json
{
  "configs": [
    {
      "installation": "DCS.release_server",
      "highscore": [
        {
          "channel": 826772687216181456,
          "params": {
            "period": "day",
            "limit": 3
          }
        },
        {
          "channel": 826772687216181456,
          "params": {
            "period": "month",
            "limit": 10
          }
        }
      ]
    }
  ]
}
```


## User Linking

It is recommended that your users link their Discord ID to their UCID (DCS World ID). The bot can try to do that by 
itself (AUTOMATCH = true), but might fail, especially, when the in-game names and Discord names of users differ a lot.
If the AUTOMATCH is disabled or was not successful, users can generate a unique TOKEN that is being sent as a DM with 
```.linkme```. The TOKEN can then be entered in the in-game chat as a chat-command with ```-linkme TOKEN```.

## Discord Commands

| Command                | Parameter                                        | Channel | Role      | Description                                                                                         |
|------------------------|--------------------------------------------------|---------|-----------|-----------------------------------------------------------------------------------------------------|
| .statistics/.stats     | [@member / DCS name / ucid] [day/week/month/all] | all     | DCS       | Display your own statistics or that of a specific member.                                           |
| .statsme               | [day/week/month/all]                             | all     | DCS       | Send your own statistics in a DM instead of displaying them in public.                              |
| .highscore/.hs         | [day/week/month/all]                             | all     | DCS       | Shows the players with the most playtime or most kills in specific areas (CAP/CAS/SEAD/Anti-Ship)   |
| .link                  | @member ucid                                     | all     | DCS Admin | Sometimes users can't be linked automatically. That is a manual workaround.                         |
| .unlink                | @member / ucid                                   | all     | DCS Admin | Unlink a member from a ucid / ucid from a user, if the automatic linking didn't work.               |
| .info                  | @member / ucid / DCS name                        | all     | DCS Admin | Displays information about that user and let you (un)ban, kick or unlink them.                      |  
| .linkcheck             |                                                  | all     | DCS Admin | Checks if a DCS user could be matched to a member.                                                  |
| .mislinks / .mislinked |                                                  | all     | DCS Admin | Checks if a DCS user is possibly mismatched with the wrong member (might still be correct though!). |
| .reset_statistics      |                                                  | all     | Admin     | Resets the statistics for this server.                                                              |
| .linkme                |                                                  | all     | DCS       | Link a discord user to a DCS user (user self-service).                                              |

{: .note }
> If a campaign is active on your server, `.stats` and `.highscore` will display the data of that campaign only, unless you use the "all" period.

## Reports

This plugin comes with 3 custom reports where 2 of them are available in two different shapes.
* userstats.json
* userstats.campaign.json (for campaign statistics)
* highscore.json
* highscore-campaign.json (for campaign statistics)
* info.json

All templates can be amended if copied into /reports/userstats.

## How to disable Userstats inside of missions

Sometimes you don't want your mission to generate per-user statistics, but you don't want to configure your server to disable them forever?
Well, then - just disable them from inside your mission:

```lua
  dofile(lfs.writedir() .. 'Scripts/net/DCSServerBot/DCSServerBot.lua')
  dcsbot.disableUserStats()
```

## Database Tables

- [STATISTICS](../database.md#statistics)

[Server Specific Sections]: ../configuration/dcsserverbot-ini.md
