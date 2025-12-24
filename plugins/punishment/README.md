# Plugin Punishment
The DCSServerBot auto-ban, auto-kick, auto-move-back-to-spectators module, based on the player's behavior and the 
configuration described in here.<br>
The ideas of this plugin are based on [Slmod](https://github.com/mrSkortch/DCS-SLmod). Thanks to Speed for his awesome solution!

## Configuration
As Punishment is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - punishment
```

The plugin itself is configured with a file named config/plugins/punishment.yaml. You'll find a sample in ./samples:
```yaml
DEFAULT:
  channel: 1122334455667788 # Optional: Channel where to post who was punished for what (default: admin channel, disable: -1).
  penalties:                # These are the penalty points to use.
  - event: kill             # If you team-kill a human player, you get 30 points, 18 in the case of an AI.
    human: 30
    AI: 18
    action: move_to_spec
    reason: Killing a team member
  - event: collision_kill   # If you collide and kill another player, you get 20 points, if it was an AI, you get 12.
    human: 20
    AI: 12
    reason: Killing a team member during a collision
  - event: friendly_fire    # If you fire on a friendly player, you get 12 penalty points, 8 for an AI.
    human: 12
    AI: 8
    reason: Friendly fire on a team member
  - event: collision_hit    # If you hit another team member, you get 5 points, 1 if it was an AI.
    human: 5
    AI: 1
    reason: Colliding with a team member
  - event: reslot           # If you re-slotted when being shot at, you get 30 points (you stole a kill)
    default: 30
    reason: Respawning when being shot at
  forgive: 30               # People can forgive others in-between of 30 seconds (default) with the .forgive in-game chat command.
  punishments:              # list of punishments, based on the user's penalty points
  - points: 100             # we temp-ban the player when they reached 100 points.
    action: ban
    days: 3                 # ban-time in days. default: 3
  - points: 60              # we kick them from the server when their points reached 60
    action: kick
  - points: 40              # we move them to spectators when they have 40 points
    action: move_to_spec
  - penalty: 10             # we take away credits from them if they have 10 points
    action: credits
    points: 12              # number of credits to take
  - points: 1               # we warn them with each penalty point they got
    action: warn
  flightHoursWeight:        # If you want to treat people that are frequent flyers on your server differently, you can do this here
  - time: 0                 # New joiners will get 1.4x the penalty points as described below
    weight: 1.4
  - time: 3                 # people that were flying for at least 3 hours on your servers get the described penalty points 1:1
    weight: 1
  - time: 10                # people that flew longer than 10 hours get only 0.7x the penalty points (70%)
    weight: 0.7
  decay:                    # This describes how points should vanish over time
  - days: 0                 # on the initial day, we do not do anything
    weight: 1
  - days: 3                 # after 3 days, 75% of the points will still be there (25% will be wiped)
    weight: 0.75
  - days: 30                # after 30 days, an additional 75% of the points will be wiped (25% will still be there)
    weight: 0.25
  - days: 60                # after 60 days, the penalty points get wiped completely                
    weight: 0
DCS.dcs_serverrelease:
  exemptions:
    ucid:
      - 'aabbccddee'          # Do not punish the users with these UCIDs
      - 'eeggffjjjs'
    discord: 
      - '@everyone'           # Do not punish members of your Discord (that are linked) on this server
      - 'Moderators'          # Do not punish your own moderators (Discord role, not bot role!) on this server
```
### Penalties
The number of penalty points that a player "earns" is configured here. 
Collisions are hits where the player's aircraft is being used as a weapon.
You can add your own events that you can use from inside the mission environment (see below), like the example here 
with "zone-bombing".<br/>
If you use the inline "action"-element, you can already trigger any action like a "move_to_spec" or "credits" when 
someone FFs or kills a team member.

> [!NOTE]
> Multiple events that happen in-between a minute are calculated as a single event. 
> This is on purpose, to avoid too many punishments when a user unintentionally dropped a CBU onto something or 
> strafed multiple targets in one run.

### Punishments
Each point level can trigger a specific action. When the user hits this limit by gathering penalties, the specific 
action is being triggered. Actions are triggered at least every minute. So there might be a slight delay in being a bad 
pilot and getting punished. That allows victims to `-forgive` the dedicated act. 
A ban is temporary, and punishment points can decay over time (see below).<br/>

In conjunction with the [CreditSystem](../creditsystem/README.md) plugin, you can use "credits" as a punishment and take away credit points 
from players if they misbehave. A campaign has to be running for this to happen.

### Exemptions
User that should not be punished. Can be either ucids or Discord roles.

### Forgive
To prevent actions to be executed against an initiator, victims can use the `.forgive` command inside the in-game chat.
This will delete the punishments to this user that are not executed already and delete the events from this specific 
occasion.

### Weight per Flight-Hours
Weight punishment by flight hours. This will be the sum of flight hours over all servers handled by this bot.

### Decay
Penalty points will decrease over time. This is configured here.
Decay can only be configured once, so there is no need for a server-specific configuration. All other elements can be configured for every server instance differently.

> [!WARNING]
> If you change the decay function, the existing penalties might not decay anymore, depending on how you adjust the 
> values. This is unfortunately an issue with how I implemented it.
> You can reset all the penalties in your database by using this SQL:
> ```sql
> DELETE FROM pu_events;
> ```
> After that, every new punishment will decay, according to your new decay function.

## Discord Commands
| Command      | Parameter | Channel | Role            | Description                                                                                                              |
|--------------|-----------|---------|-----------------|--------------------------------------------------------------------------------------------------------------------------|
| /forgive     | user      | all     | DCS Admin       | Deletes all punishment points for this member / user.                                                                    |
| /penalty     | [user]    | all     | DCS / DCS Admin | Displays the players penalty points. [user] can only be used by DCS Admins.                                              |
| /infractions | user      | all     | DCS Admin       | Display the last (default: 10) infraction events of that user.<br>Mission statistics needs to be enabled for it to work. |

## In-Game Chat Commands
| Command  | Parameter | Role | Description                                            |
|----------|-----------|------|--------------------------------------------------------|
| -forgive |           | all  | Forgive the last actions that happened to your player. |
| -penalty |           | all  | Shows your penalty points.                             |

## How to use the penalty system inside missions
To use the penalty system inside missions, you can use this lua-function:
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
-- ...
    if condition then
        player = event.initiator.unit:getPlayerName()
        dcsbot.punish('zone-bombing', player)
    end
-- ...
```

### How to disable punishments inside missions
Sometimes you don't want your mission to punish users at all, but you don't want to configure your server to 
disable them forever. 
To do so, you can disable the punishments from inside your mission:
```lua
if dcsbot then
    dcsbot.disablePunishments()    
end 
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
