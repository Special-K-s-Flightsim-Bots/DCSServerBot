# Plugin "Missionstats"
When enabled, this plugin will generate a persistent mission statistics embed to be displayed in the status channels and detailed statistics from the ingame event system. 
The global DCSServerBot.lua and a plugin specific mission.lua will automatically be loaded into any mission running on that specific server.

## Configuration
Mission statistics can be enabled or disabled in the server configuration (see [e) Server Specific Sections](../../README.md)).
Missionstats needs the Userstats plugin to be loaded first.

## How to disable Missionstats inside of missions
To disable mission statistics for a specific mission, you can use the following piece of code somewhere in your mission (not in a on-startup trigger, but shortly after).
```lua
  dcsbot.disableMissionStats()
```

## Discord Commands
**Attention:** These commands need MISSION_STATISTICS=true in the server you want to run the commands on!


| Command                  | Parameter                        | Channel                     | Role | Description                                                                                        |
|--------------------------|----------------------------------|-----------------------------|------|----------------------------------------------------------------------------------------------------|
| .missionstats            |                                  | status-/chat-/admin-channel | DCS  | Display the current mission situation for red and blue and the achievments in kills and captures.  |
| .sorties                 | [@member / Player Name] [period] | all                         | DCS  | Display the number of sories and real flight time per module / period.                             |
| .modulestats / .modstats | [@member / Player Name] [period] | all                         | DCS  | Display module and weapon statistics per module.                                                   |
| .refuellings / .refuel   | [@member / Player Name] [period] | all                         | DCS  | Display refuellings per module.                                                                    |

**Attention:** "period" can either be a period [day, week, month, year] or a campaign name!

## Tables
### Missionstats
| Column      | Type                             | Description                                                                            |
|-------------|----------------------------------|----------------------------------------------------------------------------------------|
| #id         | SERIAL                           | Auto-incrementing unique ID of this column.                                            |
| mission_id  | INTEGER NOT NULL                 | Unique ID of this mission. FK to the missions table.                                   |
| event       | TEXT NOT NULL                    | Ingame event (see list below).                                                         |
| init_id     | TEXT                             | Initiator ID, ucid of the player or -1 for AI.                                         |
| init_side   | TEXT                             | Initiator coalition, 0 = Neutral, 1 = Red, 2 = Blue                                    |
| init_type   | TEXT                             | Initiator unit type, like "FA-18c_Hornet"                                              |
| init_cat    | TEXT                             | Initiator unit category like Airplanes, Helicopters, Ships, Ground Units               |
| target_id   | TEXT                             | Target ID, ucid of the player or -1 for AI.                                            |
| target_side | TEXT                             | Target coalition, 0 = Neutral, 1 = Red, 2 = Blue                                       |
| target_type | TEXT                             | Target unit type, like "FA-18c_Hornet"                                                 |
| target_cat  | TEXT                             | Target category like Airplanes, Helicopters, Ships, Ground Units, Structure or Unknown |
| weapon      | TEXT                             | Name of the weapon. Gun if empty.                                                      |
| place       | TEXT                             | Airfield for t/o and landing events.                                                   |
| comment     | TEXT                             | Comment for LSO ratings, BDA, etc.                                                     |
| time        | TIMESTAMP NOT NULL DEFAULT NOW() | Time of the event in local time.                                                       |
