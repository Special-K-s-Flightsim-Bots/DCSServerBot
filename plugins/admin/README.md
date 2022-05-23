# Plugin "Admin"
This plugin supports administrative commands that are needed to operate a DCS server remotely.

## Discord Commands

| Command   | Parameter             | Channel       | Role      | Description                                                                                                              |
|-----------|-----------------------|---------------|-----------|--------------------------------------------------------------------------------------------------------------------------|
| .servers  |                       | all           | DCS       | Lists all registered DCS servers and their status (same as .mission but for all). Servers will auto-register on startup. |
| .startup  |                       | admin-channel | DCS Admin | Starts a dedicated DCS server process.                                                                                   |
| .shutdown |                       | admin-channel | DCS Admin | Shuts the dedicated DCS server process down.                                                                             |
| .start    |                       | admin-channel | DCS Admin | Starts a stopped DCS server.                                                                                             |
| .stop     |                       | admin-channel | DCS Admin | Stops a DCS server.                                                                                                      |
| .status   |                       | all           | DCS Admin | Shows the status of a specific DCS server, or of all configured.                                                         |
| .update   |                       | admin-channel | DCS Admin | Updates DCS World to the latest available version.                                                                       |
| .password | [coalition]*          | admin-channel | DCS Admin | Changes the password of a DCS server or a specific coalition* on this server.                                            |
| .kick     | name [reason]         | admin-channel | DCS Admin | Kicks the user with the in-game name "name" from the DCS server. The "reason" will be provided to the user.              |
| .spec     | name [reason]         | admin-channel | DCS Admin | Moves the user with the in-game name "name" to spectators. The "reason" will be provided to the user as a chat message.  |
| .ban      | @member/ucid [reason] | all           | DCS Admin | Bans a specific player either by their Discord ID or UCID.                                                               |
| .unban    | @member/ucid          | all           | DCS Admin | Unbans a specific player either by their Discord ID or UCID.                                                             |
| .bans     |                       | all           | DCS Admin | Lists the current active bans.                                                                                           |
| .dcslog   |                       | admin-channel | DCS Admin | Send the current dcs.log as a DM (zipped, if > 8 MB).                                                                    |
| .botlog   |                       | all           | DCS Admin | Send the current dcsserverbot.log as a DM (zipped, if > 8 MB).                                                           |
| .shell    |                       | admin-channel | Admin     | Runs a shell command on a specific node.                                                                                 |

In addition, you can upload embeds to discord channels, just by using json files like this:
```json
{
	"message_id": 967120121632006228,
	"title": "Special K successfully landed at Batumi!",
	"description": "Special K did it again and succeeded at his try to land at Senaki.",
	"img": "https://i.chzbgr.com/full/8459987200/hB315ED4E/damn-instruction-manual",
	"fields": {
		"Pilot": "sexy as hell",
		"Speed": "130 kn",
		"Wind": "calm"
	},
	"footer": "Just kidding, they forgot to put their gear down!"
}
```
Just upload a file with such a content and a .json extension to the channel where you want the information to be 
displayed (can be done by the Admin-Role only!). If you provide a valid message_id, the message will be updated.

*) DCS 2.7.12 or higher

## Config File Uploads
Every config file that either the bot uses for itself (dcsserverbot.ini) or the different plugins use (_<plugin>_.json)
can be uploaded in any channel by a user belonging to the Admin group. The files will be replaced, the dedicated plugin
will be reloaded or the bot will be restarted (security question applies), if you update the dcsserverbot.ini.

## Tables
### Bans
| Column    | Type                             | Description                                          |
|-----------|----------------------------------|------------------------------------------------------|
| #ucid     | TEXT NOT NULL                    | Unique ID of this player. FK to the players table.   |
| banned_by | TEXT NOT NULL                    | User name that banned or DCSServerBot for auto bans. |
| reason    | TEXT                             | Reason for the ban.                                  |
| banned_at | TIMESTAMP NOT NULL DEFAULT NOW() | When was that user banned.                           |
