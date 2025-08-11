# Plugin RestAPI
This API provides a very simple RestAPI that you can user together with the [WebService](../../services/webservice/README.md).

## Configuration
As RestAPI is an optional plugin, you need to activate it in main.yaml first like so:
```yaml
opt_plugins:
  - restapi
```

All you can set in your config/plugins/restapi.yaml for now is a prefix that should be added to the API endpoints:
```yaml
DEFAULT:
  prefix: /stats    # use this prefix (optional)
```
## RestAPI
The following commands are available through the API

| API               | GET / POST | Parameters                                            | Description                                                                           |
|-------------------|------------|-------------------------------------------------------|---------------------------------------------------------------------------------------|
| /serverstats      | GET        |                                                       | A comprehensive statistic for your whole setup.                                       |
| /servers          | GET        |                                                       | Status for each server.                                                               |
| /getuser          | POST       | nick: string                                          | Return a list of players ordered by last seen that match this nick.                   |
| /stats            | POST       | nick: string, date: date                              | Statistics of this player                                                             |
| /highscore        | GET        | [server_name: string], [period: string], [limit: int] | Highscore output                                                                      |
| /topkills         | GET        | [limit: int]                                          | Top x of players ordered by kills descending.                                         |
| /topkdr           | GET        | [limit: int]                                          | Same as /topkills but ordered by AAKDR descending.                                    |
| /trueskill        | GET        | [limit: int]                                          | Top x trueskill ratings.                                                              |
| /weaponpk         | POST       | nick: string, date: date                              | Probability of kill for each weapon per given user.                                   |
| /credits          | POST       | nick: string, date: date, [campaign]                  | Credits of a specific player.                                                         |
| /traps            | POST       | nick: string, date: string, [limit: int]              | Lists the traps of that user.                                                         |
| /squadrons        | GET        |                                                       | Lists all squadrons.                                                                  |
| /squadron_members | POST       | name: string                                          | Lists the members of the squadron with that name.                                     |
| /squadron_credits | POST       | name: string, [campaign]                              | Lists the members of the squadron with that name.                                     |
| /linkme           | POST       | discord_id: string, force: bool                       | Same as /linkme in discord. Returns a new token that can be used in the in-game chat. |

> [!NOTE]
> To get a more detailled API-documentation, please enable debug in your WebService config and 
> access https://localhost:9876/docs.
