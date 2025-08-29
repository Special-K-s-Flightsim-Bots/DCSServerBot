---
layout: default
title: README
nav_section: plugins/userstats
---

# Plugin "UserStats"
DCSServerBot comes with a built-in, database driven statistics system. It allows users to show their own 
achievements like k/d-ratio, flight times per module, server or map, etc. For server owners, it allows you to see which 
of your servers and missions are being used most, at which time and from which kind of users (Discord members vs. 
public players).

## Squadrons
If you want to group people to see group stats, etc., you can create squadrons with DCSServerBot.<br>
Commands to manage squadrons are describe below. You can select squadrons in the period parameter of highscore and
other statistics commands where it makes sense.

## Configuration
The plugin can be configured via yaml in config/plugins/userstats.yaml. If such a file does not exist, create one.

```yaml
DEFAULT:
  wipe_stats_on_leave: true # wipe user statistics if they leave your Discord server (default: true)
  squadrons:
    self_join: true         # enable self-join for squadrons (default: true, otherwise you need to get the associated role)
    persist_list: true      # Show a persistent list in the squadron channel that updates with any join / leave
    highscore:
      params:
        limit: 10
  highscore:  # overall persistent highscore display (optional)
    channel: 1122334455667788
    params:
      period: month   # can be one of day, month, quarter, year, or any campaign name
      limit: 10       # number of players per entry
DCS.release_server:
  highscore:  # server-specific persistent highscore (optional)
  - channel: 9988776655443322
    params:
      period: day     # display a daily highscore in another channel for this server
      limit: 3        # only 3 players this time ("top 3 of the day")
  - channel: 1234567812345678
    params:
      period: month   # and a monthly statistic in another channel
      limit: 10       # "top 10 of the month"
  - channel: 9876543298765432
    params:
      mission_end: true # post the mission highscore at mission end
instance2:
  enabled: false  # we disable statistics gathering on instance2
```

## Discord Commands

| Command            | Parameter                        | Channel       | Role                         | Description                                                                                       |
|--------------------|----------------------------------|---------------|------------------------------|---------------------------------------------------------------------------------------------------|
| /statistics        | [user] [period]                  | all           | DCS                          | Display your own statistics or that of a specific user. A period can be supplied.                 |
| /highscore         | [server] [period]                | all           | DCS                          | Shows the players with the most playtime or most kills in specific areas (CAP/CAS/SEAD/Anti-Ship) |
| /reset_statistics  | [server]                         | admin-channel | Admin                        | Deletes the statistics. If a server is provided, only this server is affected.                    |
| /delete_statistics | <user>                           | all           | DCS Admin                    | Delete statistics of a user.                                                                      |
| /squadron create   | <name> <locked> [role] [channel] | all           | DCS Admin                    | Create a new squadron and give it an optional auto-role and persistent channel.                   |
| /squadron add      | <name> <user>                    | all           | DCS Admin                    | Adds a user to a squadron.                                                                        |
| /squadron delete   | <name> [user]                    | all           | DCS Admin                    | Deletes a user from a squadron or a whole squadron.                                               |
| /squadron lock     | <name>                           | all           | DCS Admin                    | Locks a squadron (no users can join or leave anymore on their own).                               |
| /squadron unlock   | <name>                           | all           | DCS Admin                    | Unlocks a squadron again.                                                                         |
| /squadron join     | <name>                           | all           | DCS                          | Join a squadron (and get the optional auto role).                                                 |
| /squadron leave    | <name>                           | all           | DCS                          | Leave a squadron (and remove the optional auto role).                                             |
| /squadron list     | <name>                           | all           | DCS                          | Lists the members of a squadron.                                                                  |
| /squadron credits  | <name>                           | all           | Squadron Admins / GameMaster | Display the squadrons credits (see [Credit System](../creditsystem/README.md)                     |

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
- yyyymmdd-yyyymmdd
- yyyymmdd HH:MM:SS-yyyymmdd HH:MM:SS (and any variation without seconds, minutes)
- -3 days (or years, weeks, months, hours, minutes)

In addition, you can provide any campaign name, mission name, squadron name or theatre like so:
- campaign:My Fancy Campaign
- mission:Foothold
- theatre:Caucasus or terrain:Caucasus
- squadron:Tomcatters

> [!NOTE]
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
if dcsbot then
    dcsbot.disableUserStats()
end 
```

## Tables
### Statistics
| Column             | Type                | Description                                                                                                                    |
|--------------------|---------------------|--------------------------------------------------------------------------------------------------------------------------------|
| #mission_id        | INTEGER NOT NULL    | Unique ID of this mission. FK to the missions table.                                                                           |
| #player_ucid       | TEXT NOT NULL       | Unique ID of this player. FK to the players table.                                                                             |
| #slot              | TEXT NOT NULL       | Unit type of this slot. Slots that end with "(Crew)" are sub slots in multicrew units.                                         |
| side               | INTEGER DEFAULT 0   | Side: 0 = Spectator, 1 = Red, 2 = Blue                                                                                         |
| kills              | INTEGER DEFAULT 0   | Total number of kills. Team-kills or self-kills are not counted.                                                               |
| pvp                | INTEGER DEFAULT 0   | Number of pvp-only kills. A pvp kill is a human player kill of another coalition.                                              |
| ejections          | INTEGER DEFAULT 0   | Increased when you eject. Will not be counted for multi-crew atm, as there is a bug in DCS.                                    |
| crashes            | INTEGER DEFAULT 0   | Increased when your plane crashes.                                                                                             |
| teamkills          | INTEGER DEFAULT 0   | Number of FF incidents, either against players or AI.                                                                          |
| kills_planes       | INTEGER DEFAULT 0   | Increased on every kill of unit-type Airplane.                                                                                 |
| kills_helicopters  | INTEGER DEFAULT 0   | Increased on every kill of unit-type Helicopter.                                                                               |
| kills_ships        | INTEGER DEFAULT 0   | Increased on every kill of unit-type Ship.                                                                                     |
| kills_sams         | INTEGER DEFAULT 0   | Increased on every kill of unit-type Air Defence.                                                                              |
| kills_ground       | INTEGER DEFAULT 0   | Increased on every kill of unit-type Ground Unit.                                                                              |
| deaths             | INTEGER DEFAULT 0   | Increased when the pilot dies. If you manage to eject and land safely, no death is counted.<br/>Team-kills don't count deaths. |
| deaths_pvp         | INTEGER DEFAULT 0   | Increased, when you die in a PVP fight (no FF).                                                                                |
| deaths_planes      | INTEGER DEFAULT 0   | Increased, when you got killed by a plane.                                                                                     |
| deaths_helicopters | INTEGER DEFAULT 0   | Increased, when you got killed by a helicopter.                                                                                |
| deaths_shops       | INTEGER DEFAULT 0   | Increased, when you got killed by a ship.                                                                                      |
| deaths_sams        | INTEGER DEFAULT 0   | Increased, when you got killed by AA.                                                                                          |
| death_ground       | INTEGER DEFAULT 0   | Increased, when you got killed by a ground unit.                                                                               |
| takeoffs           | INTEGER DEFAULT 0   | Number of takeoffs. Subsequent takeoffs inbetween one minute are counted as one takeoff (workaround DCS bug).                  |
| landings           | INTEGER DEFAULT 0   | Number of landings. Subsequent landings inbetween one minute are counted as one landing (workaround DCS bug).                  |
| #hop_on            | TIMESTAMP NOT NULL  | Time the player occupied this unit.                                                                                            |
| hop_off            | TIMESTAMP           | Time, the player left this unit or the server.                                                                                 |

### Squadrons
| Column      | Type                           | Description                                            |
|-------------|--------------------------------|--------------------------------------------------------|
| #id         | INTEGER NOT NULL               | Unique ID of this squadron. PK to the squadrons table. |
| name        | TEXT NOT NULL                  | Name of the squadron.                                  |
| description | TEXT                           | Description of the squadron.                           |
| role        | BIGINT                         | Optional: Role ID of a squadron-role                   |
| image_url   | TEXT                           | Optional: URL of the squadron-logo                     |
| channel     | BIGINT                         | Optional: ID of a squadron channel                     |
| locked      | BOOLEAN NOT NULL DEFAULT FALSE | True: Squadron is locked.                              |

### Squadron_Members
| Column       | Type             | Description                                            |
|--------------|------------------|--------------------------------------------------------|
| #squadron_id | INTEGER NOT NULL | Unique ID of this squadron. FK to the squadrons table. |
| player_ucid  | TEXT NOT NULL    | UCID of a squadron member.                             |
