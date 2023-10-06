# Plugin "GameMaster"
The GameMaster plugin adds commands that help you to interact with a running mission by either different kinds of 
messaging, setting and clearing flags or even running lua scripts directly in your mission from out of Discord. 
You can enable the campaign system in here, too, that can be used in different other plugins, like CreditSystem, 
Slotblocking or Userstats.

## Discord Commands

| Command              | Parameter                             | Channel       | Roles                 | Description                                                                                      |
|----------------------|---------------------------------------|---------------|-----------------------|--------------------------------------------------------------------------------------------------|
| /chat                | message                               | admin-channel | DCS Admin             | Sends a message to the DCS in-game-chat.                                                         |
| /popup               | red/blue/all/player message [timeout] | admin-channel | DCS Admin, GameMaster | Sends a popup to the dedicated coalition or player* in game with an optional timeout.            |
| /broadcast           | red/blue/all                          | admin-channel | DCS Admin, GameMaster | Like /popup but to all running servers.                                                          |
| /flag                | name [value]                          | admin-channel | DCS Admin, GameMaster | Sets (or clears) a flag inside the running mission or returns the current value.                 |
| /variable            | name [value]                          | admin-channel | DCS Admin, GameMaster | Sets (or gets) a mission variable.                                                               |
| /do_script           | lua code                              | admin-channel | DCS Admin, GameMaster | Runs specific lua code inside the running mission.                                               |
| /do_script_file      | file                                  | admin-channel | DCS Admin, GameMaster | Loads a script (relative to Saved Games\DCS...) into the running mission.                        |
| /reset_coalitions    |                                       | all           | DCS Admin             | Resets all user-coalition-bindings on all servers.                                               |
| /campaign list       | [active]                              | admin-channel | DCS Admin, GameMaster | Lists all available campaigns. If "active" is provided, only active campaigns will be displayed. |
| /campaign info       | campaign                              | admin-channel | DCS Admin, GameMaster | Displays information about a campaign like name, description, start, stop and involved servers.  |
| /campaign add        |                                       | admin-channel | DCS Admin, GameMaster | Create a new campaign.                                                                           |
| /campaign add_server | campaign server                       | admin-channel | DCS Admin, GameMaster | Add a server to an existing campaign.                                                            |
| /campaign start      | campaign                              | admin-channel | DCS Admin, GameMaster | Starts a new campaign with the provided name, if none is running.                                |
| /campaign stop       | campaign                              | admin-channel | DCS Admin, GameMaster | Stops the given campaign.                                                                        |
| /campaign delete     | [campaign]                            | admin-channel | DCS Admin, GameMaster | Deletes a campaign out of the list. If no name is provided the current campaign will be deleted. |

## In-Game Chat Commands

| Command    | Parameter     | Role                  | Description                    |
|------------|---------------|-----------------------|--------------------------------|
| .join      | \<coalition\> | all                   | Join a coalition.              |
| .leave     |               | all                   | Leave a coalition.             |
| .red       |               | all                   | Join the red coalition.        |
| .blue      |               | all                   | Join the blue coalition.       |
| .coalition |               | all                   | Shows your current coalition.  |
| .password  |               | all                   | Shows your coalition password. |
| .flag      | flag [value]  | DCS Admin, GameMaster | Reads or sets a flag.          |

## Upload of (persistent) Embeds

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
displayed (can be done by the Admin-Role only!). If you provide a valid message_id (right click, Copy Message ID), 
this specific message will be updated.

## Usage inside of Missions (Scripting API)
You can enable, disable (= delete) and reset (= delete + start) campaigns inside of missions, too. If you want to use 
the system and for instance reset it on every mission start, you just need to put in the following lines in one of 
your triggers that fire after a mission load:
```lua
  dofile(lfs.writedir() .. 'Scripts/net/DCSServerBot/DCSServerBot.lua')
  -- [...]
  dcsbot.resetCampaign() -- remove, if you want to keep the points for players
  -- or -- dcsbot.startCampaign() -- starts a new campaign (if there is not one started already)
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
| start       | TIMESTAMP DEFAULT NOW() | The start-time of the campaign (default = now). |
| stop        | TIMESTAMP               | When will the campaign stop.                    |

### CAMPAIGN_SERVERS
| Column       | Type          | Description        |
|--------------|---------------|--------------------|
| #campaign_id | TEXT NOT NULL | The campaign name. |
| #server_name | TEXT NOT NULL | The server name.   |


### COALITIONS
| Column          | Type                    | Description                                     |
|-----------------|-------------------------|-------------------------------------------------|
| #server_name    | TEXT NOT NULL           | The respective server name.                     |
| #player_ucid    | TEXT NOT NULL           | The players UCID.                               |
| coalition       | TEXT                    | "red", "blue" or empty.                         |
| coalition_leave | TIMESTAMP               | Time when the last coalition was left.          |
