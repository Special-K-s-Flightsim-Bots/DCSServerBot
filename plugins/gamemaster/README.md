# Plugin "GameMaster"
The gamemaster plugin adds commands that help you to interact with a running mission by either different kinds of 
messaging or setting and clearing flags. You can enable the campaign system in here, too, that can be used in different
other plugins, like Slotblocking or Userstats.

## Discord Commands

| Command           | Parameter                             | Channel             | Roles                 | Description                                                                                                       |
|-------------------|---------------------------------------|---------------------|-----------------------|-------------------------------------------------------------------------------------------------------------------|
| .chat             | message                               | chat-/admin-channel | DCS                   | Sends a message to the DCS in-game-chat.                                                                          |
| .popup            | red/blue/all/player [timeout] message | admin-channel       | DCS Admin, GameMaster | Sends a popup to the dedicated coalition or player* in game with an optional timeout.                             |
| .broadcast        | red/blue/all                          | all                 | DCS Admin, GameMaster | Like .popup but to all running servers.                                                                           |
| .flag             | name [value]                          | admin-channel       | DCS Admin, GameMaster | Sets (or clears) a flag inside the running mission or returns the current value.                                  |
| .variable         | name [value]                          | admin-channel       | DCS Admin, GameMaster | Sets (or gets) a mission variable.                                                                                |
| .do_script        | lua code                              | admin-channel       | DCS Admin, GameMaster | Runs specific lua code inside the running mission.                                                                |
| .do_script_file   | file                                  | admin-channel       | DCS Admin, GameMaster | Loads a script (relative to Saved Games\DCS...) into the running mission.                                         |
| .reset_coalitions |                                       | all                 | DCS Admin             | Resets all user-coalition-bindings on all servers.                                                                |
| .campaign         | add <name> [start] [stop]             | admin-channel       | DCS Admin, GameMaster | Creates a new campaign "name", starting at "start" and ending at "stop".                                          |
| .campaign         | start <name>                          | admin-channel       | DCS Admin, GameMaster | Starts a new campaign with the provided name, if none is running.                                                 |
| .campaign         | stop                                  | admin-channel       | DCS Admin, GameMaster | Stops the current campaign.                                                                                       |
| .campaign         | delete [name]                         | admin-channel       | DCS Admin, GameMaster | Deletes a campaign out of the list. If no name is provided the current campaign will be deleted.                  |
| .campaign         | list [-all]                           | admin-channel       | DCS Admin, GameMaster | Lists all available campaigns. If "-all" is not provided (default), only campaigns from now on will be displayed. |

*) DCS 2.7.12 or higher

In addition, you can upload embeds to discord channels, just by using json files like this:

```json
{
  "message_id": 967120121632006228,
  "title": "Special K successfully landed at Senaki!",
  "description": "Special K did it again and succeeded at his try to land at Senaki.",
  "img": "https://i.chzbgr.com/full/8459987200/hB315ED4E/damn-instruction-manual",
  "fields": [
    {
      "name": "Pilot",
      "value": "sexy as hell",
      "inline": true
    },
    {
      "name": "Speed",
      "value": "130 kn",
      "inline": true
    },
    {
      "name": "Wind",
      "value": "calm",
      "inline": true
    }
  ],
  "footer": "Just kidding, they forgot to put their gear down!"
}
```

Just upload a file with such a content and a .json extension to the channel where you want the information to be 
displayed (can be done by the Admin-Role only!). If you provide a valid message_id, the message will be updated.

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

### COALITIONS
| Column          | Type                    | Description                                     |
|-----------------|-------------------------|-------------------------------------------------|
| #server_name    | TEXT NOT NULL           | The respective server name.                     |
| #player_ucid    | TEXT NOT NULL           | The players UCID.                               |
| coalition       | TEXT                    | "red", "blue" or empty.                         |
| coalition_leave | TIMESTAMP               | Time when the last coalition was left.          |
