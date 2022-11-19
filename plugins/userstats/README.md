# Plugin "UserStats"
DCSServerBot comes with a built in, database driven statistics system. It allows either users to show their own achievements like k/d-ratio, flighttimes per module, server or map, etc.
For server owners, it allows you to see which of your servers and missions are being used most, at which time and from which kind of users (Discord members vs. public players).

## Configuration
User statistics can be enabled or disabled in the server configuration (see [e) Server Specific Sections](../../README.md)).
Userstats needs the Mission plugin to be loaded first.

## User Linking
It is recommended that your users link their Discord ID to their UCID (DCS World ID). The bot can try to do that by 
itself (AUTOMATCH = true), but might fail, especially, when the ingame names and Discord names of users differ a lot.
If the AUTOMATCH is disabled or was not successful, users can generate a unique TOKEN that is being sent as a DM with 
```.linkme```. The TOKEN can then be entered in the in-game chat as a chat-command with ```-linkme TOKEN```.

## Discord Commands

| Command                | Parameter                                 | Channel | Role      | Description                                                                                         |
|------------------------|-------------------------------------------|---------|-----------|-----------------------------------------------------------------------------------------------------|
| .statistics/.stats     | [@member / DCS name] [day/week/month/all] | all     | DCS       | Display your own statistics or that of a specific member.                                           |
| .statsme               | [day/week/month/all]                      | all     | DCS       | Send your own statistics in a DM instead of displaying them in public.                              |
| .highscore/.hs         | [day/week/month/all]                      | all     | DCS       | Shows the players with the most playtime or most kills in specific areas (CAP/CAS/SEAD/Anti-Ship)   |
| .link                  | @member ucid                              | all     | DCS Admin | Sometimes users can't be linked automatically. That is a manual workaround.                         |
| .unlink                | @member / ucid                            | all     | DCS Admin | Unlink a member from a ucid / ucid from a user, if the automatic linking didn't work.               |
| .info                  | @member / ucid / DCS name                 | all     | DCS Admin | Displays information about that user and let you (un)ban, kick or unlink them.                      |  
| .linkcheck             |                                           | all     | DCS Admin | Checks if a DCS user could be matched to a member.                                                  |
| .mislinks / .mislinked |                                           | all     | DCS Admin | Checks if a DCS user is possibly mismatched with the wrong member (might still be correct though!). |
| .reset_statistics      |                                           | all     | Admin     | Resets the statistics for this server.                                                              |
| .linkme                |                                           | all     | DCS       | Link a discord user to a DCS user (user self-service).                                              |

**ATTENTION**:<br/>
If a campaign is active on your server, .stats and .highscore will display the data of that campaign only, unless you use
the "all" period.

## How to disable Userstats inside of missions
Sometimes you don't want your mission to generate per-user statistics, but you don't want to configure your server to disable them forever?
Well, then - just disable them from inside your mission:
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
