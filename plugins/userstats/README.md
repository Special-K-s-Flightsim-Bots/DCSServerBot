# Plugin "UserStats"
DCSServerBot comes with a built-in, database driven statistics system. It allows either users to show their own 
achievements like k/d-ratio, flighttimes per module, server or map, etc. For server owners, it allows you to see which 
of your servers and missions are being used most, at which time and from which kind of users (Discord members vs. 
public players).

## Configuration
The plugin can be configured via yaml in config/plugins/userstats.yaml. If such a file does not exists, create one.

```yaml
DEFAULT:
  wipe_stats_on_leave: true:  # wipe user statistics if they leave your Discord server (default: true)
  highscore:  # overall persistent highscore display (optional)
    channel: 1122334455667788
    params:
      period: month   # can be one of day, month, quarter, year, or any campaign name
      limit: 10       # number of players per entry
DCS.openbeta_server:
  highscore:  # server-specific persistent highscore (optional)
  - channel: 9988776655443322
    params:
      period: day     # display a daily highscore in another channel for this server
      limit: 3        # only 3 players this time ("top 3 of the day")
  - channel: 1234567812345678
    params:
      period: month   # and a monthly statistic in another channel
      limit: 10       # "top 10 of the month"
instance2:
  enabled: false  # we disable statistics gathering on instance2
```

## User Linking
It is recommended that your users link their Discord ID to their UCID (DCS World ID). The bot can try to do that by 
itself (bot.yaml: `automatch: true`), but might fail, especially, when the in-game names and Discord names of users differ a lot.
> Users can generate a unique TOKEN that is being sent as a DM with the ```/linkme``` command.<br>
> The TOKEN can then be entered in the in-game chat as a chat-command with ```-linkme TOKEN```.

## Discord Commands

| Command             | Parameter         | Channel       | Role           | Description                                                                                         |
|---------------------|-------------------|---------------|----------------|-----------------------------------------------------------------------------------------------------|
| /statistics         | [user] [period]   | all           | DCS            | Display your own statistics or that of a specific user. A period can be supplied.                   |
| /highscore          | [server] [period] | all           | DCS            | Shows the players with the most playtime or most kills in specific areas (CAP/CAS/SEAD/Anti-Ship)   |
| /link               | @member player    | all           | DCS Admin      | Sometimes users can't be linked automatically. This is the manual workaround.                       |
| /unlink             | user              | all           | DCS Admin      | Unlink a member from a ucid / ucid from a user, if the automatic linking made a mistake.            |
| /info               | user              | all           | DCS Admin      | Displays information about that user and let you (un)ban, kick or unlink them.                      |  
| /linkcheck          |                   | all           | DCS Admin      | Checks if a DCS user could be matched to a member.                                                  |
| /mislinks           |                   | all           | DCS Admin      | Checks if a DCS user is possibly mismatched with the wrong member (might still be correct though!). |
| /linkme             |                   | all           | DCS            | Link a discord user to a DCS user (user self-service).                                              |
| /inactive           | period number     | admin-channel | DCS Admin      | Show users that are inactive for a specific amount of time.                                         |
| /reset_statistics   | [server]          | admin-channel | Admin          | Deletes the statistics. If a server is provided, only this server is affected.                      |
| /delete_statistics  | [user]            | all           | DCS, DCS Admin | Lets a user delete their own statistics, or an DCS Admin do it for any user.                        |

### Periods
Periods can be used to specify, if you only want to see statistics for a specific time-period.
This can be either a fixed period like a day or a year or a campaign.

Supported periods:
- day
- week
- month
- year
- today
- yesterday
- all

In addition you can provide any campaign name (which have to be different from the periods, so please don't name your
campaign "day" or "year").

> ⚠️ **Attention!**<br/>
> If a campaign is active on your server, `/statistics` and `/highscore` will display the data of that campaign only, 
> unless you use the "all" period.

## Reports
This plugin comes with 4 custom reports where 2 of them are available in two different shapes.
* userstats.json
* userstats.campaign.json (for campaign statistics)
* highscore.json
* highscore-campaign.json (for campaign statistics)
* info.json
* inactive.json

All templates can be amended if copied into /reports/userstats.

## How to disable Userstats inside of missions
Sometimes you don't want your mission to generate per-user statistics, but you don't want to configure your server to 
disable them forever. To do so, you can just disable the statistics gathering from inside your mission:
```lua
  dofile(lfs.writedir() .. 'Scripts/net/DCSServerBot/DCSServerBot.lua')
  dcsbot.disableUserStats()
```

## Tables
### Statistics
| Column             | Type                | Description                                                                                                                   |
|--------------------|---------------------|-------------------------------------------------------------------------------------------------------------------------------|
| #mission_id        | INTEGER NOT NULL    | Unique ID of this mission. FK to the missions table.                                                                          |
| #player_ucid       | TEXT NOT NULL       | Unique ID of this player. FK to the players table.                                                                            |
| #slot              | TEXT NOT NULL       | Unit type of this slot. Slots that end with "(Crew)" are sub slots in multicrew units.                                        |
| side               | INTEGER DEFAULT 0   | Side: 0 = Spectator, 1 = Red, 2 = Blue                                                                                        |
| kills              | INTEGER DEFAULT 0   | Total number of kills. Teamkills or selfkills are not counted.                                                                |
| pvp                | INTEGER DEFAULT 0   | Number of pvp-only kills. A pvp kill is a human player kill of another coalition.                                             |
| ejections          | INTEGER DEFAULT 0   | Increased when you eject. Will not be counted for multicrew atm, as there is a bug in DCS.                                    |
| crashes            | INTEGER DEFAULT 0   | Increased when your plane crashes.                                                                                            |
| teamkills          | INTEGER DEFAULT 0   | Number of FF incidents, either against players or AI.                                                                         |
| kills_planes       | INTEGER DEFAULT 0   | Increased on every kill of unit-type Airplane.                                                                                |
| kills_helicopters  | INTEGER DEFAULT 0   | Increased on every kill of unit-type Helicopter.                                                                              |
| kills_ships        | INTEGER DEFAULT 0   | Increased on every kill of unit-type Ship.                                                                                    |
| kills_sams         | INTEGER DEFAULT 0   | Increased on every kill of unit-type Air Defence.                                                                             |
| kills_ground       | INTEGER DEFAULT 0   | Increased on every kill of unit-type Ground Unit.                                                                             |
| deaths             | INTEGER DEFAULT 0   | Increased when the pilot dies. If you manage to eject and land safely, no death is counted.<br/>Teamkills don't count deaths. |
| deaths_pvp         | INTEGER DEFAULT 0   | Increased, when you die in a PVP fight (no FF).                                                                               |
| deaths_planes      | INTEGER DEFAULT 0   | Increased, when you got killed by a plane.                                                                                    |
| deaths_helicopters | INTEGER DEFAULT 0   | Increased, when you got killed by a helicopter.                                                                               |
| deaths_shops       | INTEGER DEFAULT 0   | Increased, when you got killed by a ship.                                                                                     |
| deaths_sams        | INTEGER DEFAULT 0   | Increased, when you got killed by AA.                                                                                         |
| death_ground       | INTEGER DEFAULT 0   | Increased, when you got killed by a ground unit.                                                                              |
| takeoffs           | INTEGER DEFAULT 0   | Number of takeoffs. Subsequent takeoffs inbetween one minute are counted as one takeoff (workaround DCS bug).                 |
| landings           | INTEGER DEFAULT 0   | Number of landings. Subsequent landings inbetween one minute are counted as one landing (workaround DCS bug).                 |
| #hop_on            | TIMESTAMP NOT NULL  | Time the player occupied this unit.                                                                                           |
| hop_off            | TIMESTAMP           | Time, the player left this unit or the server.                                                                                |
