# Plugin Punishment
The DCSServerBot auto-ban, auto-kick, auto-move-back-to-spectators module, based on the players behaviour and the configuration described in here.
The ideas of this plugin are based on [Slmod](https://github.com/mrSkortch/DCS-SLmod). Thanks to Speed for his awesome solution!

## Configuration
The punishment is configured with a file named config\punishment.json. You'll find a sample file in that directory:
```json
{
  "configs": [
    {
      "penalties": [
        { "event": "kill", "reason": "Killing a team member", "human": 30, "AI": 18, "action": "credits", "penalty": 10 },
        { "event": "collision_kill", "reason": "Killing a team member during a collision", "human": 20, "AI": 12 },
        { "event": "friendly_fire", "reason": "Friendly fire on a team member", "human": 12, "AI": 8 },
        { "event": "collision_hit", "reason": "Colliding with a team member", "human": 5, "AI": 1 },
        { "event": "zone-bombing", "reason": "Bombing in a safe zone", "default": 50 }   -- example of a custom event
      ],
      "punishments": [
        { "points": 100, "action": "ban" },
        { "points": 60, "action": "kick" },
        { "points": 40, "action": "move_to_spec" },
        { "points": 1, "action": "warn" }
      ],
      "exemptions": [
        { "ucid": "abc123456abc987654" },
        { "discord":  "Admin" },
        { "discord":  "DCS Admin" }
      ],
      "forgive" : 30,
      "unban": 75,
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
    },
    {
      "installation": "my-pvp-server",
      "penalties": []
    }
  ]
}
```
### Penalties
The number of penalty points that a player "earns", is configured here. Collisions are hits where the players aircraft is being used as a weapon.
You can add own events that you can use from inside the mission environment (see below), like the example here with "zone-bombing".<br/>
If you use the inline "action"-element, you can already trigger any action like a "move_to_spec" or "credits" when someone
FFs or kills a team member.

**ATTENTION:** Multiple events, that happen inbetween a minute, are calculated as a single event. This is on purpose, to avoid too many punishments when a user unintentionally dropped a CBU onto something.

### Punishments
Each point level can trigger a specific action. When the user hits this limit by gathering penalties, the specific action is being triggered.
Actions are triggered at least every minute. So there might be a slight delay in being a bad pilot and getting punished. That allows victims to -forgive the dedicated act.
A ban is temporary and punishment points can decay over time (see below).<br/>
In conjunction with the [CreditSystem](../creditsystem/README.md) plugin, you can use "credits" as a punishment and take
away credit points from players if they misbehave. You need to have "creditsystem" added to your OPT_PLUGINS though to
use it.

### Exemptions
User that should not be punished. Can be either ucids or discord groups.

### Forgive
To prevent actions to be executed against an initiator, victims can use the -forgive command inside the in-game chat.
This will delete the punishments to this user that are not executed already and delete the events from this specific occasion.

### Unban
Auto-unban when the user reached <= this amount of points.

### Weight per Flighthours
Weight punishment by flight hours. This will be the sum of flight hours over all servers handled by this bot.

### Decay
Penalty points will decrease over time. This is configured here.
Decay can only be configured once, so there is no need for a server specific configuration. All other elements can be configured for every server instance differently.

## Discord Commands

| Command  | Parameter       | Channel | Role      | Description                                            |
|----------|-----------------|---------|-----------|--------------------------------------------------------|
| .forgive | <member>/<ucid> | all     | DCS Admin | Deletes all punishment points for this member / user.  |
| .penalty |                 | all     | DCS       | Displays the players penalty points.                   |

## How to use the penalty system inside of missions
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

## Tables
### pu_events
| Column      | Type                             | Description                                                         |
|-------------|----------------------------------|---------------------------------------------------------------------|
| #id         | SERIAL                           | Auto-incrementing unique ID of this column.                         |
| init_id     | TEXT NOT NULL                    | The initiators UCID.                                                |
| target_id   | TEXT                             | The victims UCID or -1 if AI.                                       |
| server_name | TEXT NOT NULL                    | The server name the event happened.                                 |
| event       | TEXT NOT NULL                    | The event that happened according to the configuration (see above). |
| points      | DECIMAL NOT NULL                 | The points for this event (changes during decay runs).              |
| time        | TIMESTAMP NOT NULL DEFAULT NOW() | The time the event occurred.                                        |
| decay_run   | INTEGER NOT NULL DEFAULT -1      | The decay runs that were processed on this line already.            |
