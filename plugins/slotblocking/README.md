# Plugin "Slotblocking"
This is a simple slot blocking plugin that can be used in two different ways (for now, more to come).
Slots can be either blocked by Discord groups (specific planes blocked for Discord Members, other ones blocked for Donators for instance) or by points that people earn by kills. So you can hop in another plane, as soon as you have killed a specific number of enemies.
_Friendly fire or self kills are not counted._

## Configuration
The slot blocking is configured with a file named config\slotblocking.json. You'll find a sample file in that directory:
```json
{
  "configs": [
    { -- this is the default section (no server name or instance name provided)
      "restricted": [           -- restrictions for CA slots, they can only be used by Discord group "Donators"
        { "unit_type": "artillery_commander", "discord": "Donators" },
        { "unit_type": "forward_observer", "discord": "Donators" },
        { "unit_type": "instructor", "discord": "Donators" },
        { "unit_type": "observer", "discord": "Donators" }
      ]
    },
    { -- this is a server specific section for the instance "DCS.openbeta_server" in this case
      "installation": "DCS.openbeta_server",
      "initial_points": 1,      -- You can give people points from the beginning (aka lifes). Default is 0.
      "use_reservations": true, -- if true, a "deposit" will be taken on hop-on and payed out on RTB, otherwise points will be credited on death 
      "restricted": [           -- restriction for specific groups of planes, based on a points system
        { "group_name": "^Rookie", "points": 10, "costs": 10 },
        { "group_name": "^Veteran", "points": 20, "crew": 5, "costs": 10 }, -- a multicrew seat (aka RIO) costs 5 points here
        { "group_name": "^Ace", "points": 50, "costs": 30 }
      ],
      "points_per_kill": [      -- How many points do we get per kill? If not set, default will be 1 point per kill
        { "default": 1 },
        { "category": "Ships", "points": 2 },
        { "category": "Air Defence", "points": 3 },
        { "category": "Planes", "unit_type": "F-14B", "type": "Player", "points": 5 },
        { "category": "Planes", "type": "AI", "points": 3 },
        { "category": "Planes", "type": "Player", "points": 4 },
        { "category": "Helicopters", "points": 3 }
      ]
    }
  ]
}
```
Each unit can be either defined by its "group_name" or "unit_name", which are substrings/[pattern](https://riptutorial.com/lua/example/20315/lua-pattern-matching) of the used names in your mission or by its "unit_type".
The restriction can either be "points" that you gain by kills or "discord", which is then a specific Discord role (in the example "Donators").
"costs" are the points you lose when you get killed in this specific aircraft and if provided.

If you use multiple entries for points_to_kill, please make sure, that you order them from specialized to non-specialized.
That means, in the above example you need to specify the plane with the unit_type first, then the planes without.
So this list will be evaluated **exactly in the order the items are listed** and the first match will count! 

To enable the points system, you need to start a "Campaign" on the specific server (see [Gamemaster](../gamemaster/README.md)).

| Command   | Parameter | Role      | Description                                                                                     |
|-----------|-----------|-----------|-------------------------------------------------------------------------------------------------|
| .credits  |           | DCS       | Displays the players campaign credits.                                                          |

## Usage inside of Missions (Scripting API)
If you want to change user points based on any mission achievements, you are good to go:
```lua
  dofile(lfs.writedir() .. 'Scripts/net/DCSServerBot/DCSServerBot.lua')
  [...]
  dcsbot.addUserPoints('Special K', 10) -- add 10 points to users "Special K"'s credits. Points can be negative.
```

## Tables
### CREDITS
| Column       | Type                       | Description                       |
|--------------|----------------------------|-----------------------------------|
| #campaign_id | SERIAL                     | ID of this campaign.              |
| #player_ucid | TEXT NOT NULL              | The UCID of the player            |
| points       | INTEGER NOT NULL DEFAULT 0 | The earned credits of this player |

## More Sample Use Case
Here are some sample use cases that show how the plugin can be used.
### One Life per User 
You die, you can't hop in again.
```json
{
  "configs": [
    {
      "initial_points": 1,
      "restricted": [
        { "group_name": ".+", "points":  1, "costs": 1, "message": "You ran out of lifes."}
      ],
      "points_per_kill": [
        { "default": 0 }
      ]
    }
  ]
}
```

### One Life per User (get new lifes per pvp kills)
```json
{
  "configs": [
    {
      "initial_points": 1,
      "restricted": [
        { "group_name": ".+", "points":  1, "costs": 1, "message": "You ran out of lifes."}
      ],
      "points_per_kill": [
        { "default": 0 },
        { "category": "Planes", "type": "Player", "points": 1 }
      ]
    }
  ]
}
```

### One Life per User (hard version)
Life will be taken if you hop in your plane already. You get it back, if you land properly on another airport, only then
you can select another slot.
```json
{
  "configs": [
    {
      "initial_points": 1,
      "use_reservations": true, 
      "restricted": [
        { "group_name": ".+", "points":  1, "costs": 1, "message": "You ran out of lifes."}
      ],
      "points_per_kill": [
        { "default": 0 }
      ]
    }
  ]
}
```