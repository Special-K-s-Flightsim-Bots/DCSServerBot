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
      "use_reservations": true, -- if true, points will be credited on hop-on and payed out on RTB, otherwise points will be credited on death 
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

To enable the points system, you need to start a "Campaign" on the specific server. To handle campaigns, you have the following commands:

| Command        | Parameter | Role      | Description                                                                                     |
|----------------|-----------|-----------|-------------------------------------------------------------------------------------------------|
| .campaign      | start     | DCS Admin | Starts a new campaign. All previous campaigns will be closed and their points will get deleted. |
| .campaign      | stop      | DCS Admin | Stops the current campaign. All points for this campaign will get deleted.                      |
| .campaign      | reset     | DCS Admin | Deletes all points for the running campaign on this server.                                     |

## Usage inside of Missions (Scripting API)
You can enable, disable and reset the plugin base slot blocking system (aka campaigns) inside of missions, too. 
So if you want to use the system and for instance reset it on every mission start, you just need to put in the following
lines in one of your triggers that fire after a mission load:
```lua
  dofile(lfs.writedir() .. 'Scripts/net/DCSServerBot/DCSServerBot.lua')
  [...]
  dcsbot.resetCampaign() -- remove, if you want to keep the points for players
  dcsbot.startCampaign() -- starts a new campaign (if there is not one started already)
```
This can for instance be used for some arena based game, which should start all over again after being restarted.
If you want to change user points based on any mission achievements, you are good to go:
```lua
  dofile(lfs.writedir() .. 'Scripts/net/DCSServerBot/DCSServerBot.lua')
  [...]
  dcsbot.addUserPoints('Special K', 10) -- add 10 points to users "Special K"'s credits. Points can be negative.
```
