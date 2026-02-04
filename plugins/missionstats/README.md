# Plugin "MissionStats"
When enabled, this plugin will generate a persistent mission statistics embed to be displayed in the status channels and 
detailed statistics from the in-game event system. The global DCSServerBot.lua and a plugin-specific mission.lua will 
automatically be loaded into any mission running on that specific server.

## Configuration
Missionstats need the Userstats plugin to be loaded first (default).

The configuration is held in config/plugins/missionstats.yaml:
```yaml
DCS.dcs_serverrelease:
  enabled: true                   # false: disable mission statistics gathering (default: true)
  display: true                   # false: don't show mission statistics in your status channel (default: true)
  persistence: true               # false: don't persist the mission statistics to database (default: true)
  persist_ai_statistics: false    # true: persist AI statistics to the database (default: false)
  event_filter:                   # Optional: do not receive these events (the events listed is the default list and will always be ignored unless defined differently!)
    - S_EVENT_MARK_ADDED
    - S_EVENT_MARK_REMOVED
    - S_EVENT_TOOK_CONTROL
    - S_EVENT_DISCARD_CHAIR_AFTER_EJECTION
    - S_EVENT_AI_ABORT_MISSION
    - S_EVENT_SHOOTING_START
    - S_EVENT_SHOOTING_END
  mission_end:                    # optional: display a final mission statistics embed at mission end
    persistent: true              # send a persistent mission end embed (default: non persistent) 
    channel: 1122334455667788     # channel to display the embed in
    title: Mission accomplished!  # alternative title (default: Mission Result)
```

> [!NOTE]
> When creating a custom event_filter, list all events that you want to EXCLUDE from being sent to the bot. 
> Note that creating a new filter starts fresh – it won't automatically include the default excluded events. 
> If you want to keep those default exclusions, you'll need to add them explicitly to your custom filter.

> [!NOTE]
> DCSServerBot creates some custom event types that are not part of DCS standard.
> You can also filter these events if you do not like them.
> - S_EVENT_CONNECT ⇒ a player connected to the server
> - S_EVENT_DISCONNECT ⇒ a player disconnected from the server
> - S_EVENT_TAXIWAY_TAKEOFF ⇒ a player took off from a part of the airbase that is not the runway
> - S_EVENT_GROUND_TAKEOFF ⇒ a player took off from the ground outside an airbase

## How to disable Missionstats inside missions
To disable mission statistics for a specific mission, you can use the following piece of code somewhere in your mission 
(not in an on-startup trigger, but shortly after).
```lua
  dcsbot.disableMissionStats()
```

## Discord Commands

| Command       | Parameter                         | Channel                     | Role | Description                                                                                        |
|---------------|-----------------------------------|-----------------------------|------|----------------------------------------------------------------------------------------------------|
| /missionstats |                                   | status-/chat-/admin-channel | DCS  | Display the current mission situation for red and blue and the achievements in kills and captures. |
| /sorties      | [@member / Player Name] [period*] | all                         | DCS  | Display the number of sorties and real flight time per module / period.                            |
| /modulestats  | [@member / Player Name] [period*] | all                         | DCS  | Display module and weapon statistics per module.                                                   |
| /refuelings   | [@member / Player Name] [period*] | all                         | DCS  | Display refuelings per module.                                                                     |
| /nemesis      | [@member / Player Name]           | all                         | DCS  | Display who killed you the most.                                                                   |
| /antagonist   | [@member / Player Name]           | all                         | DCS  | Display who you killed the most (top 5 atm).                                                       |

*) "period" can either be a period [day, week, month, year] or a [campaign](../gamemaster/README.md) name!

## Tables
### MISSIONSTATS
| Column      | Type                             | Description                                                                            |
|-------------|----------------------------------|----------------------------------------------------------------------------------------|
| #id         | SERIAL                           | Auto-incrementing unique ID of this column.                                            |
| mission_id  | INTEGER NOT NULL                 | Unique ID of this mission. FK to the missions table.                                   |
| event       | TEXT NOT NULL                    | In-game event (see list below).                                                        |
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
