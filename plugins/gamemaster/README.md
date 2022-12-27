# Plugin "GameMaster"
The gamemaster plugin adds commands that help you to interact with a running mission by either different kinds of 
messaging or setting and clearing flags. You can enable the campaign system in here, too, that can be used in different
other plugins, like Slotblocking or Userstats.

## Discord Commands

| Command         | Parameter                             | Channel             | Roles                 | Description                                                                                                       |
|-----------------|---------------------------------------|---------------------|-----------------------|-------------------------------------------------------------------------------------------------------------------|
| .chat           | message                               | chat-/admin-channel | DCS                   | Send a message to the DCS in-game-chat.                                                                           |
| .popup          | red/blue/all/player [timeout] message | admin-channel       | DCS Admin, GameMaster | Send a popup to the dedicated coalition or player* in game with an optional timeout.                              |
| .flag           | name [value]                          | admin-channel       | DCS Admin, GameMaster | Sets (or clears) a flag inside the running mission or returns the current value.                                  |
| .variable       | name [value]                          | admin-channel       | DCS Admin, GameMaster | Sets (or gets) a mission variable.                                                                                |
| .join           | red / blue                            | all                 | DCS                   | Joins either Coalition Red or Coalition Blue discord groups.                                                      |
| .leave          | [member/name/ucid]                    | all                 | DCS, DCS Admin        | Leave the current coalition. DCS Admin can force players leave their coalition.                                   |
| .do_script      | lua code                              | admin-channel       | DCS Admin, GameMaster | Run specific lua code inside the running mission.                                                                 |
| .do_script_file | file                                  | admin-channel       | DCS Admin, GameMaster | Load a script (relative to Saved Games\DCS...) into the running mission.                                          |
| .campaign       | add <name> [start] [stop]             | admin-channel       | DCS Admin, GameMaster | Creates a new campaign "name", starting at "start" and ending at "stop".                                          |
| .campaign       | start <name>                          | admin-channel       | DCS Admin, GameMaster | Starts a new campaign with the provided name, if none is running.                                                 |
| .campaign       | stop                                  | admin-channel       | DCS Admin, GameMaster | Stops the current campaign.                                                                                       |
| .campaign       | delete [name]                         | admin-channel       | DCS Admin, GameMaster | Deletes a campaign out of the list. If no name is provided the current campaign will be deleted.                  |
| .campaign       | list [-all]                           | admin-channel       | DCS Admin, GameMaster | Lists all available campaigns. If "-all" is not provided (default), only campaigns from now on will be displayed. |

*) DCS 2.7.12 or higher

## Usage inside of Missions (Scripting API)
You can enable, disable (= delete) and reset (= delete + start) the plugin base slot blocking system (aka campaigns) 
inside of missions, too. If you want to use the system and for instance reset it on every mission start, you just need 
to put in the following lines in one of your triggers that fire after a mission load:
```lua
  dofile(lfs.writedir() .. 'Scripts/net/DCSServerBot/DCSServerBot.lua')
  [...]
  dcsbot.resetCampaign() -- remove, if you want to keep the points for players
  dcsbot.startCampaign() -- starts a new campaign (if there is not one started already)
```
This can for instance be used for some arena based game, which should start all over again after being restarted.
A campaign name of "_internal_" will be used in that case.

## Tables
### CAMPAIGNS
| Column      | Type                    | Description                                     |
|-------------|-------------------------|-------------------------------------------------|
| #id         | SERIAL                  | Auto-incrementing unique ID of this campaign.   |
| name        | TEXT NOT NULL           | The campaign name.                              |
| description | TEXT                    | A brief description about the campaign.         |
| server_name | TEXT NOT NULL           | The server name the campaign is valid for.      |
| start       | TIMESTAMP DEFAULT NOW() | The start-time of the campaign (default = now). |
| stop        | TIMESTAMP               | When will the campaign stop.                    |
