---
title: CreditSystem
parent: Plugin System
nav_order: 0
---

# Plugin "CreditSystem"

This plugin adds credits to the bot. People can gain credits atm by killing stuff. It is planned to enhance this 
further, by gaining credits by flighttimes, etc.<br/>
Other plugins make use of credits. Currently, [SlotBlocking] can block slots by credits and
take credits away, if people waste airplanes. Furthermore, [Punishment] can take credit points
away due to punishment points a player gets due to teamkills or the like.

## Configuration
The Creditsystem is configured with a file named config\creditsystem.json. You'll find a sample file in that directory:
```json
{
  "configs": [
    {
      "initial_points": 1,      -- You can give people points from the beginning. Default is 0.
      "max_points": 100,        -- People can not gather more than max_points (optional).
      "points_per_kill": [      -- How many points do we get per kill? If not set, default will be 1 point per kill
        { "default": 1 },
        { "category": "Ships", "points": 2 },
        { "category": "Air Defence", "points": 3 },
        { "category": "Planes", "unit_type": "F-14B", "type": "Player", "points": 5 },
        { "category": "Planes", "type": "AI", "points": 3 },
        { "category": "Planes", "type": "Player", "points": 4 },
        { "category": "Helicopters", "points": 3 }
      ],
      "achievements": [         -- Achievements that will be given to the player after a specific playtime or points
        { "role": "Rookie", "playtime": 0, "credits": 0 },
        { "role": "Veteran",  "playtime": 25, "credits": 50, "combined": true },  -- combined=true means they have to reach both goals to get that role
        { "role": "Ace", "playtime": 50, "credits": 100, "combined": true }
      ]
    },{
      "installation": "instance2",
      "initial_points": [
        {
          "discord": "Donator",
          "points": 10
        },
        {
          "default": 1
        }
      ]
    }
  ]
}
```
In general, you get points per category. This can be specified in more detail by adding unit types or even "Player" or
"AI" as a type to give people different points for killing human players. A "default" will be used for any other kill.

If you use multiple entries for points_to_kill, please make sure, that you order them from specialized to non-specialized.
That means, in the above example you need to specify the plane with the unit_type first, then the planes without.
So this list will be evaluated **exactly in the order the items are listed** and the first match will count! 

To enable the points system, you need to start a "Campaign" on the specific server (see [Gamemaster]).
Same is true for achievements, where you can give your players Discord roles depending on either the time they were 
flying in that specific campaign or on the points they achieved. Losing points might downgrade the player again.

If you want to specify points for ground targets, you need to select the correct category out of this list:

* Unarmed
* Air Defence
* Artillery
* Armor
* Locomotive
* Carriage
* MissilesSS


## Discord Commands

| Command  | Parameter          | Role | Description                                           |
|----------|--------------------|------|-------------------------------------------------------|
| .credits |                    | DCS  | Displays the players campaign credits.                |
| .donate  | <@member> <points> | DCS  | Donate any of your campaign points to another member. |

## Usage inside of Missions (Scripting API)
If you want to change user points based on any mission achievements, you are good to go:
```lua
  dofile(lfs.writedir() .. 'Scripts/net/DCSServerBot/DCSServerBot.lua')
  [...]
  dcsbot.addUserPoints('Special K', 10) -- add 10 points to users "Special K"'s credits. Points can be negative to take them away.
```

## Database Tables

- [CREDITS](../database.md#credits)

[SlotBlocking]: slotblocking.md
[Punishment]: punishment.md
[Gamemaster]: gamemaster.md
