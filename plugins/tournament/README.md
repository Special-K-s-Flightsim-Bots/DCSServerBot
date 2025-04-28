# Plugin "Tournament"
This plugin allows you to run tournaments supported by DCSServerBot. It is based on many other concepts of the bot, so 
it needs some setup and understanding of how everything works. Let's dig into it.

## DCSServerBot Setup
You need to activate these optional plugins in your main.yaml to run tournaments:
```yaml
opt_plugins:
  - competitive # for TrueSkill™️ ratings and match handling
  - tournament  # tournament handling   
``` 

### GameMaster Role
Besides the creation of tournaments, which is a task for the Admin role, each tasks to run and modify a tournament 
require a member of the GameMaster role to run them. GameMaster is one of the bot roles, that you can set up in your
config/services/bot.yaml like so:
```yaml
roles:
  Admin:
    - 112233445566778899   # Admin
  DCS Admin:
    - 998877665544332211   # DCS Admin
  GameMaster:
    - 119922883377446655   # GameMaster
  DCS:
    - @everyone
```
> [!NOTE]
> If no GameMaster role is set, the DCS Admin role will be the GameMaster.

### Plugin Configuration
To configure the tournament plugin, create a file config\plugins\tournament.yaml like so:
```yaml
DEFAULT:
  coalition_passwords: true # set and auto-generate coalition passwords for red / blue
  allow_exports: false      # do not allow exports (default: false)
  auto_join: true           # if true, your pilot will be automatically assigned to the associated squadron (default: false)
  time_to_choose: 600       # the time squadrons have to choose their 
  presets:
    file: presets_tournament.yaml
    initial:          # presets that have to be applied to any mission
      - sanitize      # preset to be used for sanitization (security settings in mission)
      - switch_sides  # This will switch the blue and red sides on any round
    choices:  # list of preset | cost in squadron credits
      'AIM-120': 2
      'AIM-9x': 1
  channels:
    info: 112233445566778899   # information channel
    blue: 998877665544332211    # channel for blue coalition (can be the normal coalition channel)
    red: 119922883377446655     # channel for red coalition (can be the normal coalition channel)
```

You also want to configure the [Competitive plugin](../competitive/README.md). Create a config\plugins\competitive.yaml 
like so:

```yaml
DEFAULT:
  enabled: true       # Optional: we want to gather TrueSkill™️ ratings on all our instances
MyNode:
  MyInstance:         # make sure, you only enable the match configuration on the instance you want to use for the tournament!
    enabled: true
    join_on: birth    # every player joins the tournament match on join (another option: takeoff)
    win_on: rtb       # a match is won if a player of the surviving coalition brought their plane back to base. 
    end_mission: true # end the mission if the match is finished
```
> [!NOTE]
> If you end the mission if the first player RTBs, they and all other players will keep their credit points, if you have
> configured for "payback" in your slotblocking.yaml.

---

## Tournament Setup
Setting up a tournament needs some prerequisites. Please read <b>carefully</>!

### Campaign
Each tournament is based on a campaign. This means you need to create a campaign first like so:
`/campaign add`
Give it a self-explaining name and a proper description, which you want to share with your players. It will be posted,
when creating the tournament.

### Squadrons
For each participating party, a squadron needs to be created (if none exists yet). Each member that wants to fly in the
tournament needs to be set up in the squadron. Squadron admins need to be Discord members, as they need to run 
commands, squadron members not necessarily.

To create a squadron, you need to first create a role in your Discord.
Then, use `/squadron create`, give it a name and assign the role that you created earlier.
If you want nice pictures to be displayed later for the crowd (which you most likely want), assign an image also to the 
squadron.
> [!IMPORTANT]
> A squadron needs to have an assoicated role!

Then you need to add the squadron administrator, which is usually the leader of that squadron or any member that should
be able to admin this squadron (add / delete members, sign the squadron up for the tournament, etc.). You do this with
`/squadron add`.

The squadron admin is then able to add the other participants for this squadron.
> [!IMPORTANT]
> Squadron members are added by their in-game name / UCID. It is necessary that the people that should be added were
> at least once on the connected servers of the group running the tournament. Otherwise, you will not get them in the
> auto-completion list.

### Create a Tournament
If a campaign is set up, you can create a tournament with `/tournament create`. Select the respective campaign, define
the number of rounds per match and the number of players that have to fight for each side.

### Signup of Squadrons
Each squadron that wants to participate in a tournament has to sign up. A squadron admin can use `/tournament signup` 
to do so.<p>
New applications can be seen by the GameMaster role and can either be accepted with `/tournament accept` or rejected
with `/tournament reject`.

> [!NOTE]
> At the end of the signup process, your tournament has to have an equal number of squadrons!

Now, everything is set up and prepared for your upcoming tournament!

---

## Running a Tournament
We have set up everything now, squadrons have registered, and everyone is eager to start. Let's go!

### Creating Matches
A tournament consists of matches. Each match has a configurable number of rounds 
(see [Create a Tournament](#create-a-tournament)). You can create the matches on your own, and make sure that the right
squadrons fight against each other, based on time constraints, wishes, whatnot, or you can let the bot generate the 
matches for you. The bot will take each squadrons TrueSkill™️ rating and generate matches based on a snake pairing
system. This assures exciting matches, as the risk of matching a very weak squadron with a very strong one is lower.

> [!NOTE]
> If a player of a squadron never played on any of your servers, their TrueSkill™️ rating will be on default (0.0).
> The rating changes already throughout the tournament. The more tournaments you ran, the better the TrueSkill™️ rating
> will be.
> Each squadron's TrueSkill™️ rating will be calculated with a specific algorithm, based on the ratings of their members.

To create a match by yourself, use `/matches create`. To let the bot create the matches, run `/matches generate` instead. 
You can list the configured matches with `/matches list`.

### Start a Match
Use `/matches start` to start a match. This will start round one of the respective match, prepare the server for it, and
start it up.<p>
People of the respective squadrons can now join the server. The [Coalition system](../../COALITIONS.md) is enabled and
ensures that only players from the respective squadron can join. They can only join the sides they are assigned to,
and they can only join slots of these sides.

As soon as a player enters a slot, they are registered and bound to the match. Disconnecting, crashing, getting 
shot — this all will be counted as a death of the player. They are not able to rejoin the same match.

### End of a Round
A round is finished if all players of one squadron are dead and the other squadron brought at least one player
back to base. If both squadrons are dead, it is a draw.
If a round is finished, the bot stops the server and prepares the next round if no match winner is found yet.
Squadron admins can "buy" configurations for the upcoming round with squadron credits.

### Credit System
Each squadron earns credits based on the kills their players achieve. The respective configuration needs to be done in 
the [Credit System](../creditsystem/README.md). If a player earns kills throughout a match, both they and their squadron get credit points.<br>
Squadron admins can then use these credit points between two rounds to configure the next mission by paid presets. 
A preset can be more weapons, different planes, better starting positions, and whatnot. There are no limits, you need 
to configure the respective [MizEdit](../../extensions/mizedit/README.md) presets.

### End of a Match
A match is finished if all rounds were played.<br>
If one squadron scored more wins than the other squadron, this squadron wins the match.<br> 
If both squadrons scored the same number of wins or draws, a next round is being played until a winner is found.

## End of the Tournament
The tournament ends if all matches were played.

## Discord Commands
| Command              | Parameter                                    | Channel          | Role           | Description                                                       |
|----------------------|----------------------------------------------|------------------|----------------|-------------------------------------------------------------------|
| /tournament create   | campaign rounds num_players                  | admin-channel    | Admin          | Creates a new tournament, based on a campaign.                    |
| /tournament delete   | tournament                                   | admin-channel    | Admin          | Deletes the tournament and all its underlying data!               |
| /tournament signup   | tournament squadron                          | any              | Squadron Admin | Signs up this squadron to this tournament.                        |
| /tournament withdraw | tournament squadron                          | any              | Squadron Admin | Withdraws this squadron from this tournament.                     |
| /tournament accept   | tournament squadron                          | admin-channel    | GameMaster     | Accepts a squadron for this tournament.                           |
| /tournament reject   | tournament squadron                          | admin-channel    | GameMaster     | Rejects this squadron from this tournament.                       |
| /match generate      | tournament                                   | admin-channel    | GameMaster     | Auto-generates matches for each stage of the tournament.          |
| /match create        | tournament server squadron_blue squadron_red | admin-channel    | GameMaster     | Creates a match manually.                                         |
| /match list          | tournament                                   | any              | DCS            | List all matches of a specific tournament.                        |
| /match start         | tournament match [mission] [round_number]    | admin-channel    | GameMaster     | Starts a match. Prepares and starts the DCS server.               |
| /match customize     |                                              | squadron channel | Squadron Admin | Customize the next mission for the next round of a running match. |
