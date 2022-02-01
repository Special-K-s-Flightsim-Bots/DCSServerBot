### Plugin "Slotblocking"
This is a simple slot blocking plugin that can be used in two different ways (for now, more to come).
Slots can be either blocked by Discord groups (specific planes blocked for Discord Members, other ones blocked for Donators for instance) or by points that people earn by kills. So you can hop in another plane, as soon as you have killed a specific number of enemies.
_Friendly fire or self kills are not counted._

The slot blocking is configured with a file named config\slotblocking.json. You'll find a sample file in that directory:
```json
{
  "configs": [
    { -- this is the default section (no server name or instance name provided)
      "restricted": [ -- restrictions for CA slots, they can only be used by Discord group "Donators"
        { "unit_type": "artillery_commander", "discord": "Donators" },
        { "unit_type": "forward_observer", "discord": "Donators" },
        { "unit_type": "instructor", "discord": "Donators" },
        { "unit_type": "observer", "discord": "Donators" }
      ]
    },
    { -- this is a server specific section for the instance "DCS.openbeta_server" in this case
      "installation": "DCS.openbeta_server",
      "restricted": [ -- restriction for specific groups of planes, based on a points system
        { "group_name": "Rookie", "points": 10, "costs": 10 },
        { "group_name": "Veteran", "points": 20, "costs": 10 },
        { "group_name": "Ace", "points": 50, "costs": 30 }
      ],
      "points_per_kill": [ -- How many points do we get per kill? If not set, default will be 1 point per kill
        { "default": 1 },
        { "category": "Ships", "points": 2 },
        { "category": "Air Defence", "points": 3 },
        { "category": "Planes", "type": "AI", "points": 3 },
        { "category": "Helicopters", "points": 3 },
        { "category": "Planes", "type": "Player", "points": 4 },
        { "category": "Planes", "unit_type": "F-14B", "type": "Player", "points": 5 }
      ]
    }
  ]
}
```
Each unit can be either defined by its "group_name" or "unit_name", which are substrings of the used names in your mission or by its "unit_type".
The restriction can either be "points" that you gain by kills or "discord", which is then a specific Discord role (in the example "Donators").
"costs" are the points you lose when you get killed in this specific aircraft and if provided.

To enable the points system, you need to start a "Campaign" on the specific server. To handle campaigns, you have the following commands:

| Command        | Parameter | Role      | Description                                                                                     |
|----------------|-----------|-----------|-------------------------------------------------------------------------------------------------|
| .campaign      | start     | DCS Admin | Starts a new campaign. All previous campaigns will be closed and their points will get deleted. |
| .campaign      | stop      | DCS Admin | Stops the current campaign. All points for this campaign will get deleted.                      |
| .campaign      | reset     | DCS Admin | Deletes all points for the running campaign on this server.                                     |
