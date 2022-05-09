# Plugin "Gamemaster"
The gamemaster plugin adds commands that help you to interact with a running mission by either different kinds of messaging or setting and clearing flags.  

## Discord Commands

| Command         | Parameter                             | Channel             | Roles                 | Description                                                                          |
|-----------------|---------------------------------------|---------------------|-----------------------|--------------------------------------------------------------------------------------|
| .chat           | message                               | chat-/admin-channel | DCS                   | Send a message to the DCS in-game-chat.                                              |
| .popup          | red/blue/all/player [timeout] message | admin-channel       | DCS Admin, GameMaster | Send a popup to the dedicated coalition or player* in game with an optional timeout. |
| .flag           | name [value]                          | admin-channel       | DCS Admin, GameMaster | Sets (or clears) a flag inside the running mission or returns the current value.     |
| .join           | red / blue                            | all                 | DCS                   | Joins either Coalition Red or Coalition Blue discord groups.                         |
| .leave          |                                       | all                 | DCS                   | Leave the current coalition.                                                         |
| .do_script      | lua code                              | admin-channel       | DCS Admin, GameMaster | Run specific lua code inside the running mission.                                    |
| .do_script_file | file                                  | admin-channel       | DCS Admin, GameMaster | Load a script (relative to Saved Games\DCS...) into the running mission.             |

*) DCS 2.7.12 or higher
