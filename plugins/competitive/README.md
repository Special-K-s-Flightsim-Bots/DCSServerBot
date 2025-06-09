# Plugin "Competitive"
With this plugin you can increase your PvP-fun and rank players and teams according to the famous 
[TrueSkill™️](http://research.microsoft.com/en-us/projects/trueskill) ranking system.

## How does it work?
In short, every player gets a rating, based on the ratings of the people they killed or died against.
This means that you get more points if you kill someone with a higher rating, or you lose more points if you get killed 
by someone with a lower rating than yours.<br>
Especially users that are using DCSServerBot since longer have gathered a lot of PvP kills already in their databases.
On the initial installation of the Competitive plugin, DCSServerBot will read all that data and calculate a TrueSkill™️
rating for each pilot.
People that have not been involved in any PvP activities yet will get a default rating, according to the algorithm.

## How do 1vs1 engagements work?
If you enable the plugin and don't do anything else, you will get a 1vs1 rating that will change after each engagement. 
You already get some kind of team rating, when you fly in a multi-crew aircraft. As usually all members of such an 
aircraft participate in a kill (or die with the pilot in the opposite case), they will be treated as a team already. 
This means you get ranked up as a RIO as if you were the pilot of the killing aircraft.

## How do Team-Matches work?
So this is the more complex stuff. To play N vs N or N vs M, you need to register as teams. As I don't know how your 
specific missions look like, I can only assume how you do this. One way would be to create some zone where players
either spawn in or fly into. Whenever they do this, you can call a DCSServerBot function to add a member to the match:
```lua
local msg = {}
msg.command = "addPlayerToMatch"
msg.match_id = "MyUniqueMatchName"
msg.player_name = unit:getPlayerName()
dcsbot.sendBotTable(msg)
```
The bot will then add this player (and his crew if applicable) to the blue or red side of this specific match.
Whenever both sides have at least one player spawned, the match is on (specific minimum or maximum number will be added
later).
> [!NOTE]
> Look at the [Tournament-plugin](../tournament/README.md) to get an idea of how you can register on birth.

### The Match is on!
As soon as a player is part of a match, they are bound to it until the bitter end. If you re-slot, disconnect or crash,
you will be counted dead and be a loss for your team. People that got killed can change to the next match though. But
they are not allowed to join the same match again. If you try so, you get booted back to spectators.
If a RIO/WSO leaves, the pilot can still finish the match. If the pilot of a multi-crew airplane leaves, both players
are lost for that team.

Team-kills are **not** punished against the killer, as this would double-punish the remaining team members. This plugin
will automatically disable the bots [Punishment-system](../punishment/README.md) during a match! 

### The Match is over!
... when all players of one side are dead (win_on = survival), or when the surviving party brought at least one plane
back (win_on = landing). The winning team will get points, the losing team will lose them. Each player of each team will 
gain or lose points on their own accounts. There is no team rating, as teams are usually built randomly. You would even 
be rated up if you were dead. The only importance is that you were on the winning team.
To avoid any discussions, the one that died last is still a winner. So if the last two players on blue and red both
dive to the ground and hit it, the one that generates the last crash event in DCS is the winner of that round 
(if win_on is survival, otherwise it would be a draw).

## Configuration
As Competitive is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - competitive
```
If you want to disable the plugin for one or more servers, you can do it by creating a config/plugins/competitive.yaml:
```yaml
DCS.server:
  enabled: false        # optional: disable the plugin (default: true)
  silent: false         # optional: silent mode, only calculate TrueSkill:tm: ratings, but do not tell anybody about it (default: false)
  join_on: birth        # Match only: join on birth, takeoff, enter_zone (future)
  win_on: survival      # Match only: one of survival, landing, rtb, time (future), (default: survival)
  kick_on_death: false  # Match only: Kick a dead player.
  kick_time: 30         # Match only: Seconds after a dead player will be kicked.
  end_mission: false    # Match only: end the mission if a match is over (default: false)
  delayed_start: 300    # optional: give people 300 seconds to join and prepare their planes.
  win_on_noshow: 300    # optional: you win, if nobody of the opposite coalition shows up for 300 seconds
  credit_on_leave: true # Tournament only: squadrons will gain credits on a crash, disconnect, change_slot
```
> [!NOTE]
> Silent mode can only be used on simple 1vs1 engagements, not on team engagements.


You can integrate the TrueSkill™️-rating into your highscores though.<br>
To do that, you copy your /plugins/userstats/reports/highscore.json to /reports/userstats. Then replace one of the
"Graph" elements with this: 
```json
{
  "class": "plugins.competitive.reports.HighscoreTrueSkill",
  "params": { "col": 1, "row": 1 }
}
```
Select the col and row of the element you replaced (I took the "Ships" in the above example).


## Discord Commands
| Command            | Parameter        | Channel       | Role      | Description                                               |
|--------------------|------------------|---------------|-----------|-----------------------------------------------------------|
| /trueskill rating  | [user\|squadron] | all           | DCS       | Shows the TrueSkill™️ ratings of this user or squadron.   |
| /trueskill delete  | [user]           | admin-channel | DCS Admin | Deletes the TrueSkill™️ ratings of this user or everyone. |

## In-Game Chat Commands
| Command    | Parameter | Role      | Description                     |
|------------|-----------|-----------|---------------------------------|
| -trueskill |           | all       | Shows your TrueSkill™️ rating.  |

## Tables
### trueskill
| Column       | Type  | Description                                       |
|--------------|-------|---------------------------------------------------|
| #player_ucid | TEXT  | Unique ID of this user (DCS ID).                  |
| skill_mu     | FLOAT | μ of this player (see TrueSkill™️ documentation)  |
| skill_sigma  | FLOAT | σ of this player (see TrueSkill™️ documentation)  |

> [!NOTE]
> Squadron TrueSkill™️ is calculated based on the rating of each member.
