# Plugin Punishment
The DCSServerBot's auto-ban, auto-kick, auto-move-back-to-spectators module, based on the clients behaviour and the configuration described in here.
To get the best results, you want to enable the Missionstats plugin, too. Hit-Events can only be captured, when this plugin is activated. Otherwise you can only punish teamkill events.

The ideas of this plugin are based on [Slmod](https://github.com/mrSkortch/DCS-SLmod). Thanks to Speed for his awesome solution!

## Configuration
The punishment is configured with a file named config\punishment.json. You'll find a sample file in that directory:
```json
{
  "configs": [
    {
      "penalties": [
        { "event": "kill", "reason": "Killing a team member", "human": 30, "AI": 18 },
        { "event": "collision_kill", "reason": "Killing a team member during a collision", "human": 20, "AI": 12 },
        { "event": "friendly_fire", "reason": "Friendly fire on a team member", "human": 12, "AI": 8 },
        { "event": "collision_hit", "reason": "Colliding with a team member", "human": 5, "AI": 1 },
        { "event": "zone-bombing", "reason": "Bombing in a safe zone", "default": 50 }
      ],
      "punishments": [
        { "points": 100, "action": "ban", "delay": 10 },
        { "points": 60, "action": "kick", "delay": 10 },
        { "points": 40, "action": "move_to_spec", "delay": 10 },
        { "points": 1, "action": "warn" }
      ],
      "forgive" : 30,
      "flightHoursWeight": [
        { "time": 0, "weight": 1.4 },
        { "time": 3, "weight": 1 },
        { "time": 10, "weight": 0.7 }
      ],
      "decay": [
        { "days": 60, "weight": 0 },
        { "days": 30, "weight": 0.25 },
        { "days": 3, "weight": 0.75 },
        { "days": 0, "weight": 1 }
      ]
    }
  ]
}
```
### Penalties
The number of penalty points that a player "earns", is configured here. Collisions are hits where the players aircraft is being used as a weapon.
You can add own events that you can use from inside the mission environment (see below), like the example here with "zone-bombing".

### Punishments
Each point level can trigger a specific action. When the user hits this limit by gathering penalties, the specific action is being triggered.
A delay will fire this action after <delay> seconds.

A ban usually is temporary and punishment points can decay over time. After a specific number of temp bans, a user is permanently banned. 

### Forgive
To prevent actions to be executed against an initiator, victims can use the -forgive command inside the in-game chat.

### Weight per Flighthours
Weight punishment by flight hours. This will be the sum of flight hours over all servers handled by this bot.

### Decay
Penalty points will decrease over time. This is configured here.

## Hot to use the penalty system inside of missions
To use the penalty system inside of missions, you can use the commands
```lua
--[[
    eventName, the event according to the penalties table
    initiator, the player name to be punished
    target, the victim name (might be nil or -1 for AI)
]]--
dcsbot.punish(eventName, initiator, target)
```
Following the example above, a possible call could be:
```lua
[...]
    if condition then
        player = event.initiator.unit:getPlayerName()
        dcsbot.punish('zone-bombing', player)
    end
[...]
```
