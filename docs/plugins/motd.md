---
title: MOTD
parent: Plugin System
nav_order: 0
---

# Plugin "MOTD"

This plugin adds a message of the day to the server, that is displayed either on join or when you hop into a plane.

## Configuration
The plugin is configured via JSON, as many others. If you don't generate your custom json file (sample available in the 
config directory), the plugin will not generate any messages.

To be able to create a message on "birth", `MISSION_STATISTICS = true` has to be enabled on your `dcsserverbot.ini`.

```json
{
  "configs": [
    {
      "on_birth": {                                                             -- whenever a user joins a plane
        "message": "{player[name]}, welcome to {server[server_name]}!",         -- OR
        "report": "greetings.json",                                             -- report file, has to be placed in /reports/motd
        "display_type": "popup",                                                -- chat or popup
        "display_time": 20,                                                     -- only relevant for popup
        "sound": "notify.ogg"                                                   -- play this sound (has to be loaded first!)
      },
      "nudge": {
        "delay": 600,                                                           -- every 10 mins
        "message": "This awesome server is presented to you by https://discord.gg/myfancylink.\nCome and join us!",
        "recipients": "!@everyone",                                             -- who should receive it?
        "display_type": "popup",
        "display_time": 20
      }
    },
    {
      "installation": "DCS.release_server",
      "on_join": {                                                              -- whenever a user joins the server
        "message": "Welcome to our public server! Teamkills will be punished."
      }
    }
  ]
}
```

recipients can be a list of Discord groups that the player either is part of or not (!).
!@everyone means, this message is for people that are not a member of your Discord only.

If you want to play sounds, make sure that you loaded them into the mission first (see Scheduler).


### Optional Layout for multiple Recipient Groups

```json
      "nudge": {
        "delay": 60,
        "messages": [
          {
            "message": "This awesome server is presented to you by https://discord.gg/myfancylink.\nCome and join us!",
            "recipients": "!@everyone",
            "display_type": "popup",
            "display_time": 20
          },
          {
            "message": "Glad to have you guys here!",
            "recipients": "DCS Admin",
            "display_type": "popup",
            "display_time": 20
          }
        ]
      }
    }
```
