# Plugin "CreditSystem"
This plugin adds credits to the bot. People can gain credits atm by killing stuff. 

Other plugins make use of credits. Currently, [SlotBlocking](../slotblocking/README.md) can block slots by credits and
take credits away, if people waste airplanes. Furthermore, [Punishment](../punishment/README.md) can take credit points
away due to punishment points a player gets due to teamkills or the like.

## Configuration
The Creditsystem is configured with a file named config\plugins\creditsystem.yaml. You'll find a sample file in that directory:
```yaml
DEFAULT:              # valid for all servers
  initial_points: 10  # The initial points a player gets (default = 0).
  max_points: 100     # The maximum points a player can get (default = unlimited).
  points_per_kill:    # How many points do players get when they kill another unit?
  - default: 1        # You get at least one point (default = 0).
  - category: Ships   # If you kill a ship. you get 2 points
    points: 2
  - category: Air Defence
    points: 3
  - category: Planes
    type: AI          # for planes and helicopters you can decide whether you killed an AI or a human player
    points: 3
  - category: Planes
    type: Player
    points: 4
  - category: Planes
    type: Player
    unit_type: F-14B  # you can specify the unit_type, unit_name, group_name as another differentiation
    points: 5
  - category: Helicopters
    points: 3
  achievements:       # OPTIONAL: you can give players Discord roles according to their achievements
  - credits: 0
    playtime: 0       # Playtime is in hours
    role: Rookie      # Initially, with 0 credits and 0 playtime, you get the role "Rookie" (has to be in Discord)
  - credits: 50
    playtime: 25
    role: Veteran
  - credits: 100
    playtime: 50
    combined: true    # you need to have 100 credit points AND a playtime of more than 50 hrs to get the "Ace" role
    role: Ace
instance2:            # valid for a specific server
  initial_points:     # different initial points can be specified for different Discord roles
  - discord: Donator
    points: 15
  - default: 10
```
In general, you get points per category. This can be specified in more detail by adding unit types or even "Player" or
"AI" as a type to give people different points for killing human players. A "default" will be used for any other kill.

If you use multiple entries for points_to_kill, please make sure, that you order them from specialized to non-specialized.
That means, in the above example you need to specify the plane with the unit_type first, then the planes without.
So this list will be evaluated **exactly in the order the items are listed** and the first match will count! 

To enable the points system, you need to start a "Campaign" on the specific server (see [Gamemaster](../gamemaster/README.md)).
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

Achiements are possible role changes, that happen when a player either reached a specific flighttime or s specific number
of credits.

## Discord Commands
| Command         | Parameter            | Role | Description                                           |
|-----------------|----------------------|------|-------------------------------------------------------|
| /credits info   |                      | DCS  | Displays the players campaign credits.                |
| /credits donate | <@member> <donation> | DCS  | Donate any of your campaign points to another member. |

## In-Game Chat Commands

| Command  | Parameter           | Role | Description                       |
|----------|---------------------|------|-----------------------------------|
| .credits |                     | all  | Show your players credits.        |
| .donate  | whom points         | all  | Donate credits to another player. |
| .tip     | points [gci number] | all  | Tip a GCI role.                   |

## Usage inside of Missions (Scripting API)
If you want to change user points based on any mission achievements, you are good to go:
```lua
  dofile(lfs.writedir() .. 'Scripts/net/DCSServerBot/DCSServerBot.lua')
  -- [...]
  dcsbot.addUserPoints('Special K', 10) -- add 10 points to users "Special K"'s credits. Points can be negative to take them away.
```

## Tables
### CREDITS
| Column       | Type                       | Description                       |
|--------------|----------------------------|-----------------------------------|
| #campaign_id | SERIAL                     | ID of this campaign.              |
| #player_ucid | TEXT NOT NULL              | The UCID of the player            |
| points       | INTEGER NOT NULL DEFAULT 0 | The earned credits of this player |
