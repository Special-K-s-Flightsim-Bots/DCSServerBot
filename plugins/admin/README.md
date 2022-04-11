# Plugin "Admin"
This plugin supports administrative commands that are needed to operate a DCS server remotely.

## Discord Commands

| Command   | Parameter                | Channel       | Role      | Description                                                                                                              |
|-----------|--------------------------|---------------|-----------|--------------------------------------------------------------------------------------------------------------------------|
| .servers  |                          | all           | DCS       | Lists all registered DCS servers and their status (same as .mission but for all). Servers will auto-register on startup. |
| .startup  |                          | admin-channel | DCS Admin | Starts a dedicated DCS server instance (has to be registered, so it has to be started once outside of Discord).          |
| .shutdown |                          | admin-channel | DCS Admin | Shuts the dedicated DCS server down.                                                                                     |
| .update   |                          | admin-channel | DCS Admin | Updates DCS World to the latest available version.                                                                       |
| .password |                          | admin-channel | DCS Admin | Changes the password of a DCS server.                                                                                    |
| .kick     | name [reason]            | admin-channel | DCS Admin | Kicks the user with the in-game name "name" from the DCS server. The "reason" will be provided to the user.              |
| .spec     | name [reason]            | admin-channel | DCS Admin | Moves the user with the in-game name "name" to spectators. The "reason" will be provided to the user as a chat message.  |
| .ban      | @member/ucid [reason]    | all           | DCS Admin | Bans a specific player either by their Discord ID or UCID.                                                               |
| .unban    | @member/ucid             | all           | DCS Admin | Unbans a specific player either by their Discord ID or UCID.                                                             |
| .bans     |                          | all           | DCS Admin | Lists the current active bans.                                                                                           |
| .dcslog   |                          | admin-channel | DCS Admin | Send the current dcs.log as a DM (zipped, if > 8 MB).                                                                    |
| .botlog   |                          | all           | DCS Admin | Send the current dcsserverbot.log as a DM (zipped, if > 8 MB).                                                           |

## Tables
### Bans
| Column    | Type                             | Description                                          |
|-----------|----------------------------------|------------------------------------------------------|
| #ucid     | TEXT NOT NULL                    | Unique ID of this player. FK to the players table.   |
| banned_by | TEXT NOT NULL                    | User name that banned or DCSServerBot for auto bans. |
| reason    | TEXT                             | Reason for the ban.                                  |
| banned_at | TIMESTAMP NOT NULL DEFAULT NOW() | When was that user banned.                           |
