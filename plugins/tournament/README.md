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
Besides the creation of tournaments, which is a task for the Admin role, each task to run and modify a tournament 
requires a member of the GameMaster role to run them. 
GameMaster is one of the bot roles that you can set up in your config/services/bot.yaml like so:
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
  use_signup_form: false    # Do you want the participating squadrons to write an application form on signup? (default: false)
  autostart_matches: false  # If you want your matches to be autostarted by the bot (with a 1 day and 1 hour reminder per DM), set this to true (default: false)
  coalition_passwords: true # Auto-generate coalition passwords for red and blue.
  allow_exports: false      # Do not allow exports (default: false).
  auto_join: true           # If true, your pilot will be automatically assigned to the associated squadron on join (default: false).
  delayed_start: 300        # optional: give people 300 seconds to prepare their planes.
  time_to_choose: 600       # The time squadrons have to choose their customizations for the next round.
  sudden_death: false       # true: add one decisive round after the configured rounds were played if no winner was found. false: wait until the best out of X is reached.
  balance_multiplier: true  # true: use a sophisticated multiplier for credit points, based on the Trueskill™️ difference
  remove_on_death: .*       # optional: if set, any unit name that matches this regular expression will result in the removal of this unit in the next rounds of the same match
  presets:
    file: presets_tournament.yaml
    initial:                # presets that have to be applied to any mission
      - sanitize            # preset to be used for sanitization (security settings in mission)
      - switch_sides        # This will switch the blue and red sides on any round
      - random_weather      # Randomize the weather
    choices:  # list of preset | cost in squadron credits
      'AIM-120': {"costs": 2}                   # each AIM-120 costs you 2 credit points
      'AIM-9x': {"costs": 1}                    # each AIM-9x costs you 1 credit points
      'AWACS': {"costs": 0, "max": 1, "ticket": "AWACS"}  # each AWACS costs you no credit points but one AWACS ticket. You can only choose one AWACS per round.
    tickets:                # you can get a specific number of tickets per tournament
      AWACS: 2
  channels:
    info: 112233445566778899      # information channel
    streamer: 91827364519283745   # channel for a tournament streamer
    category: 119922883377446655  # a category where all match channels will be created into
    admin: 998877665544332211     # optional: gamemaster admin channel (if not set, a central admin channel will be used)
```

> [!NOTE]
> The balance_multiplier adds some fairness into your game. It will award a party that is weaker with more credit points
> and the stronger party with less. So the weaker party can buy more stuff on the next round than the stronger party.
> The system is based on an "upset bonus system" which allows multipliers between 0.5 and 2.5, depending on the
> situation.

> [!IMPORTANT]
> The remove_on_death needs you to have single unit groups in your mission.

> [!WARNING]
> The streamer channel above will receive all information about what's going on in your match. 
> You do NOT want any of the participating parties to have access to this channel!

You also want to configure the [Competitive plugin](../competitive/README.md). 
Create a config\plugins\competitive.yaml like so:

```yaml
DEFAULT:
  enabled: true         # Optional: we want to gather TrueSkill™️ ratings on all our instances
MyNode:
  MyInstance:           # make sure, you only enable the match configuration on the instance you want to use for the tournament!
    enabled: true
    join_on: birth      # every player joins the tournament match on join (another option: takeoff)
    win_on: rtb         # a match is won if a player of the surviving coalition brought their plane back to base. 
```

---

## Tournament Setup
Setting up a tournament needs some prerequisites. Please read <b>carefully</>!

### Campaign
Each tournament is based on a campaign. This means you need to create a campaign first like so:
`/campaign add`
Give it a self-explaining name and a proper description, which you want to share with your players. It will be posted
when creating the tournament.

> [!NOTE]
> The name and description of the campaign will be the name and description of the tournament!

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

> [!NOTE]
> If you add a picture to each squadron, you make your posts more colorful.

Then you need to add the squadron administrator, which is usually the leader of that squadron or any member that should
be able to admin this squadron (add / delete members, sign the squadron up for the tournament, etc.). You do this with
`/squadron add`.

The squadron admin is then able to add the other participants for this squadron.
> [!IMPORTANT]
> Squadron members are added by their in-game name / UCID. It is necessary that the people that should be added were
> at least once on the connected servers of the group running the tournament. Otherwise, you will not get them in the
> auto-completion list.<br>
> To counter that, you can enable "auto_join" in your tournament.yaml, which will auto-add each player to a squadron
> on join.

> [!WARNING]
> A player can only be a member of ONE squadron at a time!

### Create a Tournament
If a campaign is set up, you can create a tournament with `/tournament create`. Select the respective campaign, define
the number of rounds per match and the number of players that have to fight for each side.

### Signup of Squadrons
Each squadron that wants to participate in a tournament has to sign up. A squadron admin can use `/tournament signup` 
to do so.<p>
New applications can be seen by the GameMaster role and can either be accepted or rejected with `/tournament verify`.

> [!NOTE]
> At the end of the signup process, your tournament has to have an equal number of squadrons!

Now, everything is set up and prepared for your upcoming tournament!

---

## Running a Tournament
We have set everything up now, squadrons have registered, and everyone is eager to start. Let's go!

### Creating Matches
A tournament consists of matches. Each match has a configurable number of rounds 
(see [Create a Tournament](#create-a-tournament)). You can create the matches on your own and make sure that the right
squadrons fight against each other, based on time constraints, wishes, whatnot, or you can let the bot generate the 
matches for you. 

The bot has two options for match generation:

a) Group Phase<br>
Usually, tournaments start with a group phase first, where each squadron of each group fights against each other.
You can configure the number of groups where each group has to have at least two members. The bot will auto-generate
the correct number of matches for you and assigns each squadron into the correct group.

b) Elimination Phase<br>
This phase will either be your first phase, if you decide to not have a group phase, or as the first phase already,
especially for smaller tournaments.<br>
As we have information about the squadron's skills, we can make use of that when generating the matches.
The bot will take each squadron's TrueSkill™️ rating and generate matches based on a snake pairing
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
People of the assigned squadrons can now join the server. The [Coalition system](../../COALITIONS.md) is enabled and
ensures that only players from these squadrons can join. They can only join the sides they are assigned to,
and they can only join slots of these sides.

As soon as a player enters a slot, they are registered and bound to the match. Disconnecting, crashing, getting 
killed, ejecting — this all will be counted as a death of the player. They are not able to rejoin the same match.

### End of a Round
A round is finished if all players of one squadron are dead and the other squadron brought at least one player
back to base. If both squadrons are dead, it is a draw.
If a round is finished, the bot prepares the server for the next round if no match winner is found yet.
Squadron admins can "buy" configurations for the upcoming round with squadron credits by using `/match customize`.

### Credit System
Each squadron earns credits based on the kills their players achieve. The respective configuration needs to be done in 
the [Credit System](../creditsystem/README.md). If a player earns kills throughout a match, both they and their squadron get credit points.<br>
Squadron admins can then use these credit points between two rounds to configure the next mission by paid presets. 
A preset can be - more weapons, different planes, better starting positions, and whatnot. There are no limits, you just
need to configure the respective [MizEdit](../../extensions/mizedit/README.md) presets.

> [!NOTE]
> If a player disconnects or somehow self-kills themselves, the other side gets the points as squadron points that this
> kill would have been worth.

### Tickets
Each squadron can get tickets on signup. You can configure them in your tournaments.yaml (see above). A ticket is a 
different credit option, which will be invalidated as soon as you use it.

### End of a Match
A match is finished if all rounds were played.<br>
If one squadron scored more wins than the other squadron, this squadron wins the match.<br> 
If both squadrons scored the same number of wins or draws, a next round is being played until a winner is found.

### End of the Phase
The group phase will end, if all matches are played. The top 2 squadrons of each group will be selected, if you run
`/match generate <elimination>`.<br>
After each elimination stage is finished, you can generate another stage (quarter-finals, semi-final, etc) until the 
final is reached, where two squadrons play together for the win.

## End of the Tournament
The tournament ends if all matches were played and only one winning squadron succeeded.

## Discord Commands
| Command                 | Parameter                                          | Channel          | Role           | Description                                                       |
|-------------------------|----------------------------------------------------|------------------|----------------|-------------------------------------------------------------------|
| /tournament create      | campaign rounds num_players                        | admin-channel    | Admin          | Creates a new tournament, based on a campaign.                    |
| /tournament delete      | tournament                                         | admin-channel    | Admin          | Deletes the tournament and all its underlying data!               |
| /tournament signup      | tournament squadron                                | any              | Squadron Admin | Signs up this squadron to this tournament.                        |
| /tournament withdraw    | tournament squadron                                | any              | Squadron Admin | Withdraws this squadron from this tournament.                     |
| /tournament verify      | tournament squadron                                | admin-channel    | GameMaster     | Accept or reject a squadron for this tournament.                  |
| /tournament bracket     | tournament                                         | admin-channel    | GameMaster     | Generate a bracket Excel file.                                    |
| /tournament preferences | \[tournament\]                                     | admin-channel    | GameMaster     | Show time and map preferences as piecharts.                       |
| /match generate         | tournament <group\|eliminate> \[num_groups\]       | admin-channel    | GameMaster     | Auto-generates matches for each stage of the tournament.          |
| /match create           | tournament stage server squadron_blue squadron_red | admin-channel    | GameMaster     | Creates a match manually.                                         |
| /match list             | tournament                                         | any              | DCS            | List all matches of a specific tournament.                        |
| /match start            | tournament match \[mission\] \[round_number\]      | admin-channel    | GameMaster     | Starts a match. Prepares and starts the DCS server.               |
| /match edit             | tournament match \[winner_squadron_id\]            | admin-channel    | GameMaster     | Edit the results of a match.                                      |
| /match customize        |                                                    | squadron channel | Squadron Admin | Customize the next mission for the next round of a running match. |
